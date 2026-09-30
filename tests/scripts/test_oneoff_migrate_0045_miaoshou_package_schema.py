"""Safety-contract tests for the human-operated migration 0045 runner."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer_unit]

SCRIPT = (
    Path(__file__).parents[2] / "scripts/oneoff_migrate_0045_miaoshou_package_schema.sh"
)


def test_script_help_is_safe_and_documents_one_command() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "ALLOW_PROD_DESTRUCTIVE=1" in result.stdout
    assert "--confirm" in result.stdout


def test_script_refuses_without_confirmation_before_loading_env() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "必须显式传入 --confirm" in result.stderr


def test_script_pins_guard_backup_migration_verification_and_worker() -> None:
    source = SCRIPT.read_text()
    assert 'TARGET="0045_miaoshou_package_schema"' in source
    assert "require_destructive_script_guard" in source
    assert 'allow_env="ALLOW_PROD_DESTRUCTIVE"' in source
    assert "source .env" in source
    assert "gzip.open" in source
    assert "sha256" in source
    assert "systemctl --user stop tts-erp-sync.service" in source
    assert '"$ALEMBIC" upgrade "$TARGET"' in source
    assert "schema / raw history / normalized rows / cleanup verified" in source
    assert "sync_worker.main run miaoshou.packages" in source
    assert "systemctl --user start tts-erp-sync.service" in source
    # The script must never silently grant its own production override.
    assert "export ALLOW_PROD_DESTRUCTIVE" not in source
    assert "ALLOW_PROD_DESTRUCTIVE=1" in "\n".join(source.splitlines()[:8])
