"""Guard for cron logistics_tracking batch size.

W1.6 invariant: each cron batch must complete before cron's HTTP_TIMEOUT
(180s) elapses, or the cron client times out while uvicorn still holds
the PG advisory lock — subsequent batches get 409 and never run, even
though sync_log eventually shows the first batch wrote successfully.

Empirically (2026-08-27) per-order cost on this shop:
    5-order batch = 11.63s → 2.33s/order (sleep + upstream + persist)

40 × 3s (p95 headroom) = 120s ≪ 180s. 80 × 2.33s = 186s ≈ timeout,
which is exactly what the live logs show: every cron round has
batch@0 FAIL in 180.0s and batch@80/160 FAIL in 0.0s with 409.

If you tune these numbers, re-measure with a fresh POST first; the
advisory lock will silently wedge the next tick if you overshoot.
"""

from __future__ import annotations

import importlib


def _safe_import(path: str, name: str):
    """Load a module even when it isn't on sys.path by default."""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return importlib.import_module(path)


def test_sync_cron_batch_size_fits_inside_http_timeout():
    """Batch size × per-order budget must stay below cron's HTTP_TIMEOUT."""
    cron = _safe_import("sync_cron", "sync_cron")
    fast = _safe_import("tdd.tts_erp_fastapi", "tts_erp_fastapi")

    per_order_budget_s = 3.0  # measured 2.33s/order, +30% headroom
    # Leave ~30s of HTTP_TIMEOUT for connect/serialize overhead and a
    # safety margin against occasionally-slow upstream orders (the
    # per-call ceiling is 60s and one outlier must not blow the budget).
    safe_budget_s = cron.HTTP_TIMEOUT - 30
    assert cron.LOGISTICS_BATCH_SIZE * per_order_budget_s <= safe_budget_s, (
        f"LOGISTICS_BATCH_SIZE={cron.LOGISTICS_BATCH_SIZE} "
        f"× {per_order_budget_s}s = {cron.LOGISTICS_BATCH_SIZE * per_order_budget_s}s "
        f"exceeds HTTP_TIMEOUT={cron.HTTP_TIMEOUT}s - 30s slack"
    )
    assert cron.LOGISTICS_BATCH_SIZE <= fast.LOGISTICS_MAX_PER_RUN_CAP, (
        f"LOGISTICS_BATCH_SIZE={cron.LOGISTICS_BATCH_SIZE} > "
        f"LOGISTICS_MAX_PER_RUN_CAP={fast.LOGISTICS_MAX_PER_RUN_CAP} "
        "— server would silently truncate each batch"
    )


def test_logistics_max_per_run_cap_is_generous():
    """Server cap must be at least the batch size; smaller caps would
    silently truncate each batch and create phantom 'saved' numbers."""
    fast = _safe_import("tdd.tts_erp_fastapi", "tts_erp_fastapi")
    assert fast.LOGISTICS_MAX_PER_RUN_CAP >= 40, (
        f"LOGISTICS_MAX_PER_RUN_CAP={fast.LOGISTICS_MAX_PER_RUN_CAP} too low; "
        "would silently drop orders past the cap"
    )
