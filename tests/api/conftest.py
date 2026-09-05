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
from sqlalchemy import delete
from sqlalchemy.orm import Session

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
def _isolate_state(db_engine, monkeypatch):
    """One autouse fixture: clear middleware state, then tear down test rows.

    Order matters: clear the auth cache BEFORE the test body runs (so a
    key inserted in the test body is queried fresh) and AFTER it
    finishes (so subsequent tests don't see cached lookups). The DB
    cleanup runs at teardown only.

    Env isolation: reset the env vars this lane cares about so a
    ``monkeypatch.setenv`` from one test can't leak into the next, and
    so the production .env (TTS_ERP_SESSION_SECRET, etc.) doesn't bleed
    behaviour into tests that didn't ask for it. ``monkeypatch.setenv``
    in setup is undone automatically at teardown.
    """
    # Setup: wipe any TEST_ rows left over from a previous run, then
    # clear cached middleware state so a freshly-inserted key is queried
    # fresh rather than served from the in-process cache.
    from tts_erp_v2.middleware import session_auth
    from tts_erp_v2.middleware.auth import clear_cache
    from tts_erp_v2.middleware.rate_limit import reset_shared

    # Browser-login env knobs: defaults test assertions expect (Secure
    # flag off, no NAT prefix). Individual tests may monkeypatch on top.
    monkeypatch.setenv("TTS_ERP_SESSION_SECURE", "0")
    monkeypatch.delenv("TTS_ERP_EXTERNAL_PREFIX", raising=False)
    # Silence the access log middleware during tests — the api_client
    # fixture builds the full v2 app, which means every TestClient
    # request would otherwise write a structured line to stderr.
    # Tests that explicitly assert on the access log can opt back in
    # per-test (``monkeypatch.setenv('TTS_ERP_ACCESS_LOG', '1')``).
    monkeypatch.setenv("TTS_ERP_ACCESS_LOG", "0")
    # Reset the login throttle too — the conftest's _isolate_state is
    # the only autouse that touches it, and it's per-test state.
    session_auth.reset_login_throttle()

    _wipe_test_rows(db_engine)
    clear_cache()
    reset_shared()
    yield
    # Teardown: clear cached middleware state again, then wipe rows.
    clear_cache()
    reset_shared()
    _wipe_test_rows(db_engine)


def _wipe_test_rows(db_engine) -> None:
    """Delete every TEST_-prefixed row from the tables Lane E may touch.

    Uses a direct ``db_engine.begin()`` connection so the wipe survives
    the test-session savepoint (handlers commit inside the request and
    their rows outlive the test's transactional rollback). Delete order
    is child → parent to respect FKs.
    """
    from sqlalchemy import text as _text

    from tts_erp_v2.db.base import Base

    api_keys_tbl = Base.metadata.tables["security.api_keys"]
    manual_costs_tbl = Base.metadata.tables["procurement.manual_product_costs"]
    channel_products_tbl = Base.metadata.tables["commerce.channel_products"]
    channel_accounts_tbl = Base.metadata.tables["commerce.channel_accounts"]
    # 2026-08-31 procurement.spu_images — RESTRICT FK from channel_products
    # requires this wipe first. See tech-doc/procurement-ui-redesign.md §9.
    # Table is created by schema_storage.sql and not registered as an ORM
    # model, so we wipe via raw text() with the same TEST_-prefixed scope.
    spu_images_wipe = _text(
        "DELETE FROM procurement.spu_images "
        "WHERE channel_product_id IN ("
        "  SELECT id FROM commerce.channel_products "
        "  WHERE external_product_id LIKE 'TEST_%'"
        ") OR channel_account_id IN ("
        "  SELECT id FROM commerce.channel_accounts "
        "  WHERE external_account_id LIKE 'TEST_%'"
        ")"
    )

    with db_engine.begin() as conn:
        conn.execute(spu_images_wipe)
        conn.execute(
            delete(manual_costs_tbl).where(
                manual_costs_tbl.c.channel_product_id.in_(
                    select_func(channel_products_tbl.c.id).where(
                        channel_products_tbl.c.external_product_id.like("TEST_%")
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
        conn.execute(delete(api_keys_tbl).where(api_keys_tbl.c.name.like("TEST_%")))


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
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    plaintext = f"ttserp_{role}_{prefix}"
    p = _params_for_key(plaintext, role, "active")
    # Upsert on key_hash: deterministic TEST_ keys may survive a killed / aborted
    # run (teardown wipe never ran), and a leftover row would otherwise trip the
    # unique index and 401-cascade every later api test in the process. Overwriting
    # the stale row makes the fixture idempotent / self-healing.
    sess.execute(
        pg_insert(Base_metadata_api_keys())
        .values(
            key_hash=p["h"],
            key_prefix=p["p"],
            name=p["n"],
            role=p["r"],
            status=p["s"],
        )
        .on_conflict_do_update(
            index_elements=[Base_metadata_api_keys().c.key_hash],
            set_={
                "key_prefix": p["p"],
                "name": p["n"],
                "role": p["r"],
                "status": p["s"],
            },
        )
    )
    sess.commit()
    return plaintext


def _make_disabled(sess, prefix: str) -> str:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    plaintext = f"ttserp_disabled_{prefix}"
    p = _params_for_key(plaintext, "admin", "disabled")
    sess.execute(
        pg_insert(Base_metadata_api_keys())
        .values(
            key_hash=p["h"],
            key_prefix=p["p"],
            name="TEST_disabled",
            role="admin",
            status="disabled",
        )
        .on_conflict_do_update(
            index_elements=[Base_metadata_api_keys().c.key_hash],
            set_={
                "key_prefix": p["p"],
                "name": "TEST_disabled",
                "role": "admin",
                "status": "disabled",
            },
        )
    )
    sess.commit()
    return plaintext


def _key_fixture(make):
    """Each test gets a fresh API key, written with a REAL commit.

    The app under test authenticates via its own connections, so the key
    row must be truly committed (the shared ``db_session`` fixture is
    savepoint-rolled-back and therefore invisible to the app). The
    autouse ``_isolate_state`` cleanup wipes TEST_* rows afterwards.
    """

    def _fixture(db_engine) -> Iterator[str]:
        with Session(db_engine) as sess:
            key = make(sess)
        yield key

    return pytest.fixture()(_fixture)


readonly_key = _key_fixture(lambda s: _make_active(s, "readonly", "test_ro_only"))
readwrite_key = _key_fixture(lambda s: _make_active(s, "readwrite", "test_rw_only"))
admin_key = _key_fixture(lambda s: _make_active(s, "admin", "test_admin_only"))
bad_key = _key_fixture(lambda s: _make_disabled(s, "test_disabled_only"))
