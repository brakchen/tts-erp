#!/usr/bin/env python3
"""Oneoff: trigger miaoshou.common_collect_box against dev DB + show impact.

Loads .env for MIAOSHOU_* / TTS_ERP_DB_URL, prints before/after counts of
SPU / procurement_products / source_unit_cost, then runs the job with
the real MiaoshouErpClient and commits the session.

Idempotent: upsert on (procurement_account_id, external_product_id).

Usage:
    /home/schan/tts-erp/.venv/bin/python scripts/oneoff_trigger_common_collect_box.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path("/home/schan/tts-erp")
for line in (REPO / ".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(REPO))
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from tts_erp_v2.db.base import get_engine, reset_for_testing  # noqa: E402
from tts_erp_v2.jobs.miaoshou._common import (  # noqa: E402
    miaoshou_client_factory,
    resolve_miaoshou_context,
)
from tts_erp_v2.jobs.miaoshou.common_collect_box import (  # noqa: E402
    sync_common_collect_box,
)

DB_URL = os.environ["TTS_ERP_DB_URL"]

_COUNT_SQL = text(
    """
    SELECT
        (SELECT count(*) FROM commerce.products_spu)                              AS spu_total,
        (SELECT count(*) FROM commerce.products_spu
             WHERE status = 'ACTIVATE')                                            AS spu_active,
        (SELECT count(*) FROM procurement.procurement_products)                    AS pp_total,
        (SELECT count(*) FROM procurement.procurement_products
             WHERE external_product_id NOT LIKE 'TEST%')                           AS pp_real,
        (SELECT count(*) FROM procurement.procurement_products
             WHERE source_item_id IS NOT NULL)                                     AS pp_with_source_item,
        (SELECT count(*) FROM procurement.procurement_products
             WHERE source_unit_cost IS NOT NULL)                                   AS pp_with_cost,
        (SELECT count(*) FROM procurement.procurement_products pp
             JOIN commerce.products_spu cp ON cp.spu_id = pp.external_product_id
             WHERE cp.status = 'ACTIVATE')                                          AS linked_active_spu,
        (SELECT count(*) FROM reporting.product_cost_snapshots
             WHERE cost_method = 'SOURCE_PRICE')                                    AS snapshots_source
    """
)


def counts(eng) -> dict[str, int]:
    with eng.connect() as c:
        return dict(c.execute(_COUNT_SQL).mappings().one())


def main() -> None:
    reset_for_testing()
    eng = get_engine(DB_URL)

    print("=== BEFORE ===")
    before = counts(eng)
    for k in sorted(before):
        print(f"  {k:>22} = {before[k]}")

    print("\n=== triggering miaoshou.common_collect_box (real client) ===")
    with Session(eng) as s:
        ctx = resolve_miaoshou_context(s)
        if ctx is None:
            print("  ERROR: no miaoshou credentials row; aborting")
            sys.exit(1)
        client = miaoshou_client_factory(ctx)
        result = sync_common_collect_box(s, client=client)
        s.commit()
    print(f"  result: {result}")

    print("\n=== AFTER ===")
    after = counts(eng)
    for k in sorted(after):
        delta = after[k] - before[k]
        sign = "+" if delta > 0 else ("" if delta == 0 else str(delta))
        print(f"  {k:>22} = {after[k]:>5}  (Δ {sign}{delta})")


if __name__ == "__main__":
    main()
