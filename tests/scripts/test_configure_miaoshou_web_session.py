"""Safety contract for encrypted Miaoshou browser-session configuration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer_unit]

SCRIPT = Path(__file__).parents[2] / "scripts/configure_miaoshou_web_session.py"


def test_requires_explicit_confirmation() -> None:
    result = subprocess.run(
        ["python3", str(SCRIPT), "--account-id", "TEST"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "--confirm is required" in result.stderr


def test_rejects_world_readable_secret_file(tmp_path: Path) -> None:
    cookie = tmp_path / "cookie.txt"
    zebra = tmp_path / "zebra.txt"
    cookie.write_text("TEST_cookie")
    zebra.write_text("TEST_zebra")
    cookie.chmod(0o644)
    zebra.chmod(0o600)
    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--account-id",
            "TEST",
            "--cookie-file",
            str(cookie),
            "--zebra-file",
            str(zebra),
            "--confirm",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "chmod 600" in result.stderr


def test_uses_shared_encrypted_credential_service() -> None:
    source = SCRIPT.read_text()
    assert "upsert_credentials" in source
    assert 'provider="miaoshou_web"' in source
    assert "plaintext_access_token=cookie" in source
    assert "plaintext_refresh_token=zebra" in source
    assert "print(cookie)" not in source
    assert "print(zebra)" not in source
