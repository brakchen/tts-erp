"""Tests for the prod-shape destructive guard (2026-09-13).

Verifies the single source of truth in ``tts_erp_v2/api/deps.py``:
- ``is_prod_shaped_db`` returns the right bool for every dbname shape.
- ``require_destructive_guard`` short-circuits on test/dev dbnames
  and 403s on prod-shape dbnames.
- ``require_destructive_script_guard`` allows dry-run previews on
  prod, refuses ``--confirm`` on prod without the opt-in env var,
  and is a no-op on test/dev.
- The opt-in env var (``ALLOW_PROD_DESTRUCTIVE=1``) lets prod-shape
  destructive ops proceed with a banner.

Note: tests run against the dedicated ``tts_erp_v3_test`` DB
(``scripts/test.sh`` sources ``.env.test``); for the simulated-prod
cases we temporarily set ``TTS_ERP_DB_URL`` to a prod-shape value,
then restore the test URL.
"""

from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

from tts_erp_v2.api.deps import (
    is_prod_shaped_db,
    require_destructive_guard,
    require_destructive_script_guard,
)

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_unit]


# ─── is_prod_shaped_db truth table ──────────────────────────────────


@pytest.mark.parametrize(
    "dbname, expected",
    [
        ("tts_erp", True),                # bare prod
        ("tts_erp_prod", True),           # alt prod
        ("tts_erp_prod_2026", True),       # dated prod snapshot
        ("tts_erp_v3_test", False),       # test db (the one we use)
        ("myapp_test", False),            # generic test
        ("myapp_dev", False),             # generic dev
    ],
)
def test_is_prod_shaped_db_classification(dbname: str, expected: bool) -> None:
    """Every dbname maps to the right prod-shape bool.

    Covers the two prod-shape patterns from ``_PROD_SHAPED_DBNAMES`` /
    ``tts_erp_prod_`` prefix, plus the canonical test dbname and a
    couple of non-prod decoys to make sure the detector isn't overly
    aggressive.
    """
    original = os.environ.get("TTS_ERP_DB_URL")
    os.environ["TTS_ERP_DB_URL"] = f"postgresql://u:p@h:5432/{dbname}"
    try:
        assert is_prod_shaped_db() is expected
    finally:
        if original is None:
            os.environ.pop("TTS_ERP_DB_URL", None)
        else:
            os.environ["TTS_ERP_DB_URL"] = original


def test_is_prod_shaped_db_unset_is_fail_closed() -> None:
    """Missing ``TTS_ERP_DB_URL`` → True (refuse)."""
    original = os.environ.pop("TTS_ERP_DB_URL", None)
    try:
        assert is_prod_shaped_db() is True
    finally:
        if original is not None:
            os.environ["TTS_ERP_DB_URL"] = original


def test_is_prod_shaped_db_unparseable_is_fail_closed() -> None:
    """Malformed URL → True (refuse)."""
    original = os.environ.get("TTS_ERP_DB_URL")
    os.environ["TTS_ERP_DB_URL"] = "not-a-real-postgres-url://@@@"
    try:
        assert is_prod_shaped_db() is True
    finally:
        if original is None:
            os.environ.pop("TTS_ERP_DB_URL", None)
        else:
            os.environ["TTS_ERP_DB_URL"] = original


# ─── require_destructive_guard (FastAPI endpoint variant) ──────────


class _FakeRequest:
    """Minimal Request stub — the guard only reads ``request.scope``."""
    scope: dict = {}


def test_require_destructive_guard_noop_on_test_db() -> None:
    """In the real test-db environment (set by ``scripts/test.sh``),
    ``require_destructive_guard`` returns silently — no exception."""
    # We're already pointing at tts_erp_v3_test (set by conftest.py +
    # scripts/test.sh). No exception expected.
    require_destructive_guard(_FakeRequest(), op_name="noop")


def test_require_destructive_guard_raises_on_simulated_prod() -> None:
    """With ``TTS_ERP_DB_URL`` pointing at prod and no opt-in env var,
    the guard raises 403 with a message that names the op and the env
    var."""
    original_url = os.environ.get("TTS_ERP_DB_URL")
    original_optin = os.environ.get("ALLOW_PROD_DESTRUCTIVE")
    os.environ["TTS_ERP_DB_URL"] = "postgresql://u:p@h:5432/tts_erp"
    os.environ.pop("ALLOW_PROD_DESTRUCTIVE", None)
    try:
        with pytest.raises(HTTPException) as exc_info:
            require_destructive_guard(_FakeRequest(), op_name="intercept_config.delete")
        assert exc_info.value.status_code == 403
        assert "intercept_config.delete" in str(exc_info.value.detail)
        assert "ALLOW_PROD_DESTRUCTIVE" in str(exc_info.value.detail)
    finally:
        if original_url is None:
            os.environ.pop("TTS_ERP_DB_URL", None)
        else:
            os.environ["TTS_ERP_DB_URL"] = original_url
        if original_optin is not None:
            os.environ["ALLOW_PROD_DESTRUCTIVE"] = original_optin


def test_require_destructive_guard_opt_in_bypasses_on_prod() -> None:
    """With ``ALLOW_PROD_DESTRUCTIVE=1``, the guard is a no-op even on
    prod-shape dbnames (the operator has explicitly accepted the
    risk)."""
    original_url = os.environ.get("TTS_ERP_DB_URL")
    original_optin = os.environ.get("ALLOW_PROD_DESTRUCTIVE")
    os.environ["TTS_ERP_DB_URL"] = "postgresql://u:p@h:5432/tts_erp"
    os.environ["ALLOW_PROD_DESTRUCTIVE"] = "1"
    try:
        # No exception expected.
        require_destructive_guard(_FakeRequest(), op_name="intercept_config.delete")
    finally:
        if original_url is None:
            os.environ.pop("TTS_ERP_DB_URL", None)
        else:
            os.environ["TTS_ERP_DB_URL"] = original_url
        if original_optin is None:
            os.environ.pop("ALLOW_PROD_DESTRUCTIVE", None)
        else:
            os.environ["ALLOW_PROD_DESTRUCTIVE"] = original_optin


# ─── require_destructive_script_guard (script/alembic variant) ──────


def test_script_guard_noop_on_test_db() -> None:
    """Test db → no exception."""
    # Real test-db env is already set. Both confirmation=True and
    # confirmation=False must be no-ops.
    require_destructive_script_guard(
        script_name="test", confirmation=True, dangerous=True,
    )
    require_destructive_script_guard(
        script_name="test", confirmation=False, dangerous=False,
    )


def test_script_guard_dry_run_allowed_on_prod(capsys) -> None:
    """``dangerous=False`` (dry-run preview) on prod prints a banner
    but does not exit. Operators can preview destructive SQL without
    an opt-in."""
    original_url = os.environ.get("TTS_ERP_DB_URL")
    original_optin = os.environ.get("ALLOW_PROD_DESTRUCTIVE")
    os.environ["TTS_ERP_DB_URL"] = "postgresql://u:p@h:5432/tts_erp"
    os.environ.pop("ALLOW_PROD_DESTRUCTIVE", None)
    try:
        require_destructive_script_guard(
            script_name="oneoff_finance_reset",
            confirmation=False,
            dangerous=False,
        )
        out = capsys.readouterr().out
        assert "DRY-RUN on prod-shape db" in out
        assert "oneoff_finance_reset" in out
    finally:
        if original_url is None:
            os.environ.pop("TTS_ERP_DB_URL", None)
        else:
            os.environ["TTS_ERP_DB_URL"] = original_url
        if original_optin is not None:
            os.environ["ALLOW_PROD_DESTRUCTIVE"] = original_optin


def test_script_guard_refuses_confirm_on_prod() -> None:
    """``confirmation=True`` on prod without opt-in → SystemExit(2)."""
    original_url = os.environ.get("TTS_ERP_DB_URL")
    original_optin = os.environ.get("ALLOW_PROD_DESTRUCTIVE")
    os.environ["TTS_ERP_DB_URL"] = "postgresql://u:p@h:5432/tts_erp"
    os.environ.pop("ALLOW_PROD_DESTRUCTIVE", None)
    try:
        with pytest.raises(SystemExit) as exc_info:
            require_destructive_script_guard(
                script_name="oneoff_finance_reset",
                confirmation=True,
                dangerous=True,
            )
        assert exc_info.value.code == 2
    finally:
        if original_url is None:
            os.environ.pop("TTS_ERP_DB_URL", None)
        else:
            os.environ["TTS_ERP_DB_URL"] = original_url
        if original_optin is not None:
            os.environ["ALLOW_PROD_DESTRUCTIVE"] = original_optin


def test_script_guard_opt_in_bypasses_on_prod(capsys) -> None:
    """``ALLOW_PROD_DESTRUCTIVE=1`` + ``confirmation=True`` on prod →
    banner printed, no exit."""
    original_url = os.environ.get("TTS_ERP_DB_URL")
    original_optin = os.environ.get("ALLOW_PROD_DESTRUCTIVE")
    os.environ["TTS_ERP_DB_URL"] = "postgresql://u:p@h:5432/tts_erp"
    os.environ["ALLOW_PROD_DESTRUCTIVE"] = "1"
    try:
        require_destructive_script_guard(
            script_name="oneoff_finance_reset",
            confirmation=True,
            dangerous=True,
        )
        out = capsys.readouterr().out
        assert "ALLOW_PROD_DESTRUCTIVE=1" in out
        assert "destructive op on prod-shape db" in out
    finally:
        if original_url is None:
            os.environ.pop("TTS_ERP_DB_URL", None)
        else:
            os.environ["TTS_ERP_DB_URL"] = original_url
        if original_optin is None:
            os.environ.pop("ALLOW_PROD_DESTRUCTIVE", None)
        else:
            os.environ["ALLOW_PROD_DESTRUCTIVE"] = original_optin
