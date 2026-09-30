"""Safety contract for migration 0046 runner."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer_unit]
SCRIPT = (
    Path(__file__).parents[2]
    / "scripts/oneoff_migrate_0047_miaoshou_purchase_prices.sh"
)


def test_refuses_without_confirmation() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "--confirm is required" in result.stderr


def test_script_is_guarded_and_runs_initial_job() -> None:
    source = SCRIPT.read_text()
    assert 'TARGET="0047_miaoshou_purchase_price"' in source
    assert "require_destructive_script_guard" in source
    assert 'allow_env="ALLOW_PROD_DESTRUCTIVE"' in source
    assert "configure_miaoshou_web_session.py" in source
    assert "sync_worker.main run miaoshou.purchase_price_clean" in source
    assert "systemctl --user stop tts-erp-sync.service" in source
    assert "systemctl --user start tts-erp-sync.service" in source
    assert "export ALLOW_PROD_DESTRUCTIVE" not in source
