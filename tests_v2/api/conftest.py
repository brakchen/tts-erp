"""API test fixtures: TestClient wiring + API-key helper for v2 tests.

Why this is a per-dir conftest:
- Lane E's v2 routers depend on ``tts_erp_v2.app`` + middleware. We
  construct a TestClient here and parameterize role fixtures.
- Uses FastAPI's TestClient (httpx-backed) — same path as production.

Isolation strategy:
- Each test gets a fresh session via the parent ``db_session`` fixture
  (transactional savepoint, rolled back at teardown).
- All inserts use SQLAlchemy Core ``insert(Table).values(**kwargs)``,
  the canonical parameterized shape.
- An ``autouse`` fixture runs after every test to wipe
  ``TEST_``-prefixed rows from every table this lane may touch —
  including via direct ``db_engine`` so the cleanup is OUTSIDE the
  test session's savepoint and survives the rollback. The handler
  commits inside the request, so committed rows outlive the
  savepoint; explicit cleanup restores isolation.
- The auth middleware's in-process TTL cache + the rate-limit
  shared counter are reset per-test via the same autouse fixture.
"""

from __future__ import annotations

import hashlib
import os

# Ensure tests can find the project root.
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _params_for_key(plaintext: str, role: str, status: str) -> dict:
    return {
        "h": hashlib.sha256(plaintext.encode()).hexdigest(),
        "p": plaintext[:16],
        "n": "TEST_" + role,
        "r": role,
        "s": status,
    }


@pytest.fixture(autouse=True)
def _isolate_state(db_engine):
    """One autouse fixture: clear middleware state, then tear down test rows.

    Order matters: clear the auth cache BEFORE the test body runs (so a
    key inserted in the test body is queried fresh) and AFTER it
    finishes (so subsequent tests don't see cached lookups). The DB
    cleanup runs at teardown only.
    """
    from sqlalchemy import delete

    from tts_erp_v2.db.base import Base
    from tts_erp_v2.middleware.auth import clear_cache
    from tts_erp_v2.middleware.rate_limit import reset_shared

    api_keys_tbl = Base.metadata.tables["security.api_keys"]
    manual_costs_tbl = Base.metadata.tables["procurement.manual_product_costs"]
    channel_products_tbl = Base.metadata.tables["commerce.channel_products"]
    channel_accounts_tbl = Base.metadata.tables["commerce.channel_accounts"]

    # Setup: clear cached middleware state.
    clear_cache()
    reset_shared()
    yield
    # Teardown: clear cached middleware state again, then wipe rows.
    clear_cache()
    reset_shared()
    with db_engine.begin() as conn:
        # Delete in dependency order (child → parent). SQLAlchemy Core
        # ``delete(Table).where(...)`` is the canonical parameterized
        # shape — literal ``"TEST_%"`` flows through bound params, not
        # string interpolation.
        conn.execute(
            delete(manual_costs_tbl).where(
                manual_costs_tbl.c.channel_product_id.in_(
                    select_func(channel_products_tbl.c.id).where(
                        channel_products_tbl.c.external_product_id.like(
                            "TEST_%"
                        )
                    )
                )
            )
        )
        conn.execute(
            delete(channel_products_tbl).where(
                channel_products_tbl.c.external_product_id.like("TEST_%")
            )
        )
        conn.execute(
            delete(channel_accounts_tbl).where(
                channel_accounts_tbl.c.external_account_id.like("TEST_%")
            )
        )
        conn.execute(
            delete(api_keys_tbl).where(api_keys_tbl.c.name.like("TEST_%"))
        )


def select_func(col):
    """Tiny shim around ``select(...)`` for in_() subquery construction."""
    from sqlalchemy import select

    return select(col)


def Base_metadata_api_keys():
    """Return the security.api_keys Table from Lane 0's ORM metadata."""
    from tts_erp_v2.db.base import Base

    return Base.metadata.tables["security.api_keys"]


@pytest.fixture()
def api_client(db_engine) -> Iterator[TestClient]:
    """Yield a FastAPI TestClient wired against the migrated schema."""
    from tts_erp_v2.app import build_app

    prev_mode = os.environ.get("TTS_ERP_AUTH_MODE")
    os.environ["TTS_ERP_AUTH_MODE"] = "enforce"
    app = build_app()
    with TestClient(app) as client:
        yield client
    if prev_mode is None:
        os.environ.pop("TTS_ERP_AUTH_MODE", None)
    else:
        os.environ["TTS_ERP_AUTH_MODE"] = prev_mode


@pytest.fixture()
def api_client_off(db_engine) -> Iterator[TestClient]:
    """TestClient with auth off — for /healthz probes only."""
    from tts_erp_v2.app import build_app

    prev_mode = os.environ.get("TTS_ERP_AUTH_MODE")
    os.environ["TTS_ERP_AUTH_MODE"] = "off"
    app = build_app()
    with TestClient(app) as client:
        yield client
    if prev_mode is None:
        os.environ.pop("TTS_ERP_AUTH_MODE", None)
    else:
        os.environ["TTS_ERP_AUTH_MODE"] = prev_mode


def _make_active(sess, role: str, prefix: str) -> str:
    from sqlalchemy import insert

    plaintext = f"ttserp_{role}_{prefix}"
    p = _params_for_key(plaintext, role, "active")
    sess.execute(
        insert(Base_metadata_api_keys()).values(
            key_hash=p["h"],
            key_prefix=p["p"],
            name=p["n"],
            role=p["r"],
            status=p["s"],
        )
    )
    sess.commit()
    return plaintext


def _make_disabled(sess, prefix: str) -> str:
    from sqlalchemy import insert

    plaintext = f"ttserp_disabled_{prefix}"
    p = _params_for_key(plaintext, "admin", "disabled")
    sess.execute(
        insert(Base_metadata_api_keys()).values(
            key_hash=p["h"],
            key_prefix=p["p"],
            name="TEST_disabled",
            role="admin",
            status="disabled",
        )
    )
    sess.commit()
    return plaintext


def _key_fixture(make):
    """Each test gets a fresh session + key, with autouse cleanup."""

    def _fixture(db_session) -> Iterator[str]:
        key = make(db_session)
        yield key

    return pytest.fixture()(_fixture)


readonly_key = _key_fixture(lambda s: _make_active(s, "readonly", "test_ro_only"))
readwrite_key = _key_fixture(lambda s: _make_active(s, "readwrite", "test_rw_only"))
admin_key = _key_fixture(lambda s: _make_active(s, "admin", "test_admin_only"))
bad_key = _key_fixture(lambda s: _make_disabled(s, "test_disabled_only"))
