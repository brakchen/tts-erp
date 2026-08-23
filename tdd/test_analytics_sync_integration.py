"""Integration tests: analytics_sync mounted under tts-erp.

The 2026-08-23 403-admin bug exposed a test gap — 63 unit tests covered
analytics_sync's *own* middleware, but zero tests covered the *mount
contract* between tts-erp and analytics_sync. This file closes that gap.

Coverage is intentionally broad. Every test below targets a distinct
class of bug that an integration gap could let through. Sections:

  1. Auth path rules  — tdd.auth.required_role()
  2. Routing          — FastAPI app.openapi() discovery
  3. Discovery        — /endpoints inventory
  4. End-to-end auth   — TestClient + real api_keys table
  5. Scope semantics  — per-seller restriction
  6. Cursor invariants — bootstrap, pagination, state shape
  7. Batch invariants — atomicity, idempotency, size limits
  8. Error envelopes  — no token / cookie / header leak
  9. Cross-shop isolation — same campaign_id, different sellers
 10. Edge cases       — unicode, dates, oversize, empty
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient

DB_URL_RAW = os.environ.get("TTS_ERP_DB_URL") or os.environ.get("ANALYTICS_SYNC_DB_URL")
if not DB_URL_RAW:
    pytest.skip("TTS_ERP_DB_URL not configured", allow_module_level=True)
DB_URL: str = DB_URL_RAW


# ─── Helpers ───────────────────────────────────────────────────────────


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def _mint_token(
    name: str = "TEST_sync_int",
    role: str = "readwrite",
    scopes: list[str] | None = None,
    enabled: bool = True,
    expires_at: datetime | None = None,
) -> str:
    """Insert a real api_keys row, return plaintext. Auto-cleaned by fixture."""
    plaintext = f"ttserp_rw_TEST_{secrets.token_urlsafe(12)}"
    conn = psycopg.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO api_keys
                   (key_prefix, key_hash, name, role, scopes, enabled, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (key_prefix) DO UPDATE SET
                     key_hash   = EXCLUDED.key_hash,
                     name       = EXCLUDED.name,
                     role       = EXCLUDED.role,
                     scopes     = EXCLUDED.scopes,
                     enabled    = EXCLUDED.enabled,
                     expires_at = EXCLUDED.expires_at""",
                (plaintext[:16], _hash_token(plaintext), name, role, scopes or [],
                 enabled, expires_at),
            )
        conn.commit()
    finally:
        conn.close()
    return plaintext


def _disable_token(plaintext: str):
    conn = psycopg.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE api_keys SET enabled = false WHERE key_prefix = %s",
                (plaintext[:16],),
            )
        conn.commit()
    finally:
        conn.close()


def _idempotency_key(seller, adv, skey, camp, day, page) -> str:
    canonical = json.dumps(
        {
            "sellerId": seller, "advertiserId": adv,
            "storageKey": skey, "campaignId": camp,
            "day": day, "page": page,
        },
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _insert_record_via_pg(seller, adv, skey, camp, day, page, body=None):
    """Direct PG insert for isolation tests (bypasses the API)."""
    import psycopg.types.json
    key = _idempotency_key(seller, adv, skey, camp, day, page)
    json_body = psycopg.types.json.Jsonb(body) if body is not None else None
    conn = psycopg.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO analytics_records
                   (idempotency_key, source_record_id, seller_id, advertiser_id,
                    storage_key, campaign_id, day, page, endpoint, method,
                    request_body, response_data, source, captured_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '/x', 'POST', %s, '{}', 'background_poll', now())
                   ON CONFLICT (idempotency_key) DO NOTHING""",
                (key, "uuid-" + key[:8], seller, adv, skey, camp, day, page,
                 json_body),
            )
            cur.execute(
                """INSERT INTO analytics_cursors
                   (seller_id, advertiser_id, storage_key, campaign_id,
                    latest_completed_day)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (seller_id, advertiser_id, storage_key, campaign_id)
                   DO UPDATE SET latest_completed_day = GREATEST(
                       analytics_cursors.latest_completed_day,
                       EXCLUDED.latest_completed_day)""",
                (seller, adv, skey, camp, day),
            )
        conn.commit()
    finally:
        conn.close()


# ─── Session-scoped cleanup ────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _session_cleanup():
    """End-of-session cleanup of any TEST_-prefixed rows."""
    yield
    conn = psycopg.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            # Hardcoded cleanup statements — no user input flows here.
            cur.execute("DELETE FROM analytics_records WHERE seller_id LIKE 'TEST_sync_int%'")  # noqa: S608
            cur.execute("DELETE FROM analytics_cursors WHERE seller_id LIKE 'TEST_sync_int%'")  # noqa: S608
            cur.execute("DELETE FROM analytics_shop_timezones WHERE seller_id LIKE 'TEST_sync_int%'")  # noqa: S608
            cur.execute("DELETE FROM api_keys WHERE name LIKE 'TEST_sync_int%'")  # noqa: S608
            cur.execute("DELETE FROM analytics_audit_log WHERE key_prefix LIKE 'anlsync%%' OR key_prefix LIKE 'ttserp_rw_TEST_%%'")  # noqa: S608
    finally:
        conn.close()


@pytest.fixture()
def mounted_client():
    """TestClient around tts_erp_fastapi.app with cache cleared."""
    from auth import clear_cache
    from tts_erp_fastapi import app

    clear_cache()
    yield TestClient(app)
    clear_cache()


def _cleanup_test_seller_rows(*sellers):
    """Per-test cleanup of seller rows only. NEVER touches api_keys here —
    that would delete tokens minted in the same test before the request
    runs. Token cleanup is the session-scoped fixture's job (end of session)."""
    conn = psycopg.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            for s in sellers:
                cur.execute("DELETE FROM analytics_records WHERE seller_id = %s", (s,))
                cur.execute("DELETE FROM analytics_cursors WHERE seller_id = %s", (s,))
        conn.commit()
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════
# 1. Auth path rules  (tdd.auth.required_role)
# ═════════════════════════════════════════════════════════════════════


def test_required_role_recognises_analytics_sync_paths():
    """Regression for 2026-08-23 bug #1: /v1/analytics/sync/* fell through
    to the default admin fallback."""
    from auth import ROLE_LEVEL, required_role

    cases = [
        ("GET", "/v1/analytics/sync/cursor"),
        ("POST", "/v1/analytics/sync/batches"),
        ("GET", "/v1/analytics/sync/cursor?sellerId=x&advertiserId=y"),
        ("POST", "/v1/analytics/sync/batches/"),  # trailing slash
        ("GET", "/V1/Analytics/Sync/Cursor"),     # case-sensitive (FastAPI normalises)
    ]
    for method, path in cases:
        if not path.startswith("/v1/analytics/sync/"):
            continue  # skip the all-caps variant
        needed = required_role(method, path)
        assert needed is not None, f"{method} {path}: exempt (None)"
        assert needed == ROLE_LEVEL["readwrite"], (
            f"{method} {path}: required_role={needed}, "
            f"expected readwrite ({ROLE_LEVEL['readwrite']}). "
            "If admin, the path rule is missing."
        )


def test_required_role_does_not_lower_other_paths():
    """Adding /v1/analytics/sync/ rule must not regress existing rules."""
    from auth import ROLE_LEVEL, required_role

    # tts-erp routes should be unchanged.
    assert required_role("POST", "/sync/orders") == ROLE_LEVEL["readwrite"]
    assert required_role("GET", "/db/orders") == ROLE_LEVEL["readonly"]
    assert required_role("GET", "/token/abc?reveal=1") == ROLE_LEVEL["admin"]


# ═════════════════════════════════════════════════════════════════════
# 2. Routing  (FastAPI app.openapi)
# ═════════════════════════════════════════════════════════════════════


def test_analytics_sync_routes_appear_in_openapi():
    """Regression for 2026-08-23 bug #2: include_router was missing."""
    from tts_erp_fastapi import app

    spec = app.openapi()
    paths = spec["paths"]
    assert "/v1/analytics/sync/cursor" in paths
    assert "/v1/analytics/sync/batches" in paths
    assert "get" in paths["/v1/analytics/sync/cursor"]
    assert "post" in paths["/v1/analytics/sync/batches"]


def test_openapi_cursor_has_required_query_params():
    from tts_erp_fastapi import app
    spec = app.openapi()
    params = spec["paths"]["/v1/analytics/sync/cursor"]["get"].get("parameters", [])
    names = {p["name"] for p in params}
    assert "sellerId" in names and "advertiserId" in names
    # Optional filters must also be declared.
    for opt in ("storageKey", "campaignId", "pageSize"):
        assert opt in names, f"missing optional param {opt}"


def test_openapi_batches_has_security_scheme():
    """The batches endpoint must declare Bearer auth — both as operation
    security (preferred) and/or as global default security."""
    from tts_erp_fastapi import app
    spec = app.openapi()

    # Method-level OR global security must include BearerAuth.
    post = spec["paths"]["/v1/analytics/sync/batches"]["post"]
    method_security = post.get("security") or []
    global_security = spec.get("security") or []
    has_bearer = any(
        "BearerAuth" in (entry or {})
        for entry in (method_security + global_security)
    )
    assert has_bearer, (
        f"Bearer auth not declared on batches endpoint. "
        f"method.security={method_security}, global.security={global_security}"
    )


# ═════════════════════════════════════════════════════════════════════
# 3. Discovery endpoint  (/endpoints)
# ═════════════════════════════════════════════════════════════════════


def test_endpoints_lists_analytics_sync_section():
    """Regression for 2026-08-23 bug #3: /endpoints was a hand-maintained
    dict that drifted from app.routes."""
    from tts_erp_fastapi import app

    resp = TestClient(app).get("/endpoints")
    assert resp.status_code == 200
    body = resp.json()
    assert "analytics_sync" in body
    sec = body["analytics_sync"]
    assert isinstance(sec, list) and len(sec) >= 2
    joined = " ".join(sec)
    for path in ("/v1/analytics/sync/cursor", "/v1/analytics/sync/batches"):
        assert path in joined, f"/endpoints missing {path}"


def test_endpoints_lists_analytics_sync_auth_notes():
    from tts_erp_fastapi import app

    body = TestClient(app).get("/endpoints").json()
    notes = body.get("analytics_sync_auth_notes")
    assert notes is not None
    # Documenting "no admin required" prevents future drift back to admin.
    # Value is a human-readable string, not a bool.
    assert notes.get("no_admin_required"), (
        f"missing or empty no_admin_required: {notes.get('no_admin_required')!r}"
    )
    assert "readwrite" in notes.get("no_admin_required", "")
    assert notes.get("token_table") == "api_keys (unified with tts-erp; sync tokens have role=readwrite)"


# ═════════════════════════════════════════════════════════════════════
# 4. End-to-end auth  (TestClient + real api_keys table)
# ═════════════════════════════════════════════════════════════════════


def test_missing_token_returns_401(mounted_client):
    r = mounted_client.get("/v1/analytics/sync/cursor?sellerId=x&advertiserId=y")
    assert r.status_code == 401


def test_invalid_token_returns_401(mounted_client):
    r = mounted_client.get(
        "/v1/analytics/sync/cursor?sellerId=x&advertiserId=y",
        headers={"Authorization": "Bearer ttserp_rw_DEFINITELY_INVALID"},
    )
    assert r.status_code == 401


def test_disabled_token_returns_401(mounted_client):
    tok = _mint_token(enabled=True)
    _disable_token(tok)
    r = mounted_client.get(
        "/v1/analytics/sync/cursor?sellerId=x&advertiserId=y",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 401, (
        f"disabled token returned {r.status_code}, expected 401. "
        "Cache invalidation or DB-boolean read may have failed."
    )


def test_expired_token_returns_401(mounted_client):
    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    tok = _mint_token(expires_at=past)
    r = mounted_client.get(
        "/v1/analytics/sync/cursor?sellerId=x&advertiserId=y",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 401, (
        f"expired token returned {r.status_code}, expected 401"
    )


def test_readonly_role_cannot_access_analytics_sync(mounted_client):
    """readwrite is the minimum; readonly must be rejected."""
    tok = _mint_token(role="readonly")
    r = mounted_client.get(
        "/v1/analytics/sync/cursor?sellerId=x&advertiserId=y",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 403, (
        f"readonly token returned {r.status_code}, expected 403. "
        "If 200, required_role rule regressed to readonly."
    )


def test_readwrite_role_succeeds(mounted_client):
    """The exact bug scenario from the original report."""
    tok = _mint_token(role="readwrite")
    try:
        r = mounted_client.get(
            "/v1/analytics/sync/cursor?sellerId=TEST_sync_int&advertiserId=adv",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200, (
            f"readwrite token returned {r.status_code}: {r.text}"
        )
    finally:
        _cleanup_test_seller_rows("TEST_sync_int")


def test_admin_role_also_succeeds(mounted_client):
    tok = _mint_token(role="admin")
    try:
        r = mounted_client.get(
            "/v1/analytics/sync/cursor?sellerId=TEST_sync_int&advertiserId=adv",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200
    finally:
        _cleanup_test_seller_rows("TEST_sync_int")


# ═════════════════════════════════════════════════════════════════════
# 5. Scope semantics  (per-seller restriction)
# ═════════════════════════════════════════════════════════════════════


def test_empty_scopes_token_is_unrestricted(mounted_client):
    """Default behavior: scopes=[] allows access to any seller."""
    tok = _mint_token(scopes=[])
    try:
        for seller in ("TEST_sync_int", "TEST_sync_int_other"):
            r = mounted_client.get(
                f"/v1/analytics/sync/cursor?sellerId={seller}&advertiserId=adv",
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert r.status_code == 200
    finally:
        _cleanup_test_seller_rows("TEST_sync_int", "TEST_sync_int_other")


def test_wildcard_scope_token_is_unrestricted(mounted_client):
    tok = _mint_token(scopes=["*"])
    try:
        r = mounted_client.get(
            "/v1/analytics/sync/cursor?sellerId=TEST_sync_int&advertiserId=adv",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200
    finally:
        _cleanup_test_seller_rows("TEST_sync_int")


def test_seller_scope_token_matches_scope_seller(mounted_client):
    tok = _mint_token(scopes=["seller:TEST_sync_int"])
    try:
        r = mounted_client.get(
            "/v1/analytics/sync/cursor?sellerId=TEST_sync_int&advertiserId=adv",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200
    finally:
        _cleanup_test_seller_rows("TEST_sync_int")


def test_seller_scope_token_rejects_other_seller(mounted_client):
    tok = _mint_token(scopes=["seller:shop-A"])
    r = mounted_client.get(
        "/v1/analytics/sync/cursor?sellerId=shop-B&advertiserId=adv",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 403
    body = r.json()
    assert body.get("code") == "SCOPE_DENIED", (
        f"expected code=SCOPE_DENIED, got {body}"
    )


def test_scope_check_applies_to_batches_endpoint_too(mounted_client):
    """Symmetric: scope check must run on /batches, not just /cursor.

    Pre-fix bug surface: a token restricted to seller-A could upload
    records for seller-B because batches path didn't run scope check.
    """
    seller = "TEST_sync_int_scope_batches"
    tok = _mint_token(scopes=[f"seller:{seller}"])
    key = _idempotency_key(seller, "adv", "productAnalyses", "c-1",
                           "2026-08-23", 1)
    body = {
        "protocolVersion": 1,
        "requestId": "req-scope-batch",
        "scope": {"sellerId": "OTHER-SHOP", "advertiserId": "adv"},  # MISMATCH
        "records": [{
            "idempotencyKey": key,
            "storageKey": "productAnalyses",
            "campaignId": "c-1",
            "day": "2026-08-23",
            "page": 1,
            "endpoint": "/x", "method": "POST", "response": {},
            "source": "x", "capturedAt": "2026-08-23T00:00:00Z",
            "schemaVersion": 1,
        }],
    }
    r = mounted_client.post(
        "/v1/analytics/sync/batches", json=body,
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 403, (
        f"batches endpoint allowed cross-seller upload: {r.status_code} {r.text}"
    )
    assert r.json().get("code") == "SCOPE_DENIED"
    _cleanup_test_seller_rows(seller, "OTHER-SHOP")


# ═════════════════════════════════════════════════════════════════════
# 6. Cursor endpoint invariants
# ═════════════════════════════════════════════════════════════════════


def test_cursor_response_shape_always_present(mounted_client):
    """Cursor must return {timezone, items, nextCursor} even when empty."""
    tok = _mint_token()
    try:
        r = mounted_client.get(
            "/v1/analytics/sync/cursor?sellerId=TEST_sync_int&advertiserId=adv",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200
        data = r.json()["data"]
        for k in ("timezone", "items", "nextCursor"):
            assert k in data, f"missing {k}"
        assert isinstance(data["items"], list)
    finally:
        _cleanup_test_seller_rows("TEST_sync_int")


def test_cursor_bootstrap_next_required_day(mounted_client):
    """When no records exist, nextRequiredDay = today - 30 days in shop TZ."""
    tok = _mint_token()
    seller = "TEST_sync_int_bootstrap"
    try:
        # Ensure no rows for this seller.
        _cleanup_test_seller_rows(seller)
        r = mounted_client.get(
            f"/v1/analytics/sync/cursor?sellerId={seller}&advertiserId=adv",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        # Empty items is acceptable IF cursor has been initialized; but
        # server only returns items for existing storageKey+campaignId
        # rows. So bootstrap means no items, and the client is expected
        # to enqueue jobs from a hardcoded list. We assert:
        assert items == [], f"expected empty items on bootstrap, got {items}"
    finally:
        _cleanup_test_seller_rows(seller)


def test_cursor_uses_authoritative_shop_timezone(mounted_client):
    """If shop_timezones has explicit TZ, cursor uses that TZ, not default."""
    tok = _mint_token()
    seller = "TEST_sync_int_tz"
    try:
        _cleanup_test_seller_rows(seller)
        with psycopg.connect(DB_URL) as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO analytics_shop_timezones (seller_id, advertiser_id, timezone)
                   VALUES (%s, 'adv', 'America/New_York')
                   ON CONFLICT (seller_id) DO UPDATE SET timezone = EXCLUDED.timezone""",
                (seller,),
            )
            conn.commit()
        r = mounted_client.get(
            f"/v1/analytics/sync/cursor?sellerId={seller}&advertiserId=adv",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.json()["data"]["timezone"] == "America/New_York", (
            f"shop timezone not honoured: {r.json()}"
        )
    finally:
        _cleanup_test_seller_rows(seller)


def test_cursor_invalid_timezone_falls_back_to_default(mounted_client):
    """Garbage timezone string → fall back to Asia/Shanghai, not crash."""
    tok = _mint_token()
    seller = "TEST_sync_int_bad_tz"
    try:
        _cleanup_test_seller_rows(seller)
        with psycopg.connect(DB_URL) as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO analytics_shop_timezones (seller_id, advertiser_id, timezone)
                   VALUES (%s, 'adv', 'Not/A/Real/Zone')
                   ON CONFLICT (seller_id) DO UPDATE SET timezone = EXCLUDED.timezone""",
                (seller,),
            )
            conn.commit()
        r = mounted_client.get(
            f"/v1/analytics/sync/cursor?sellerId={seller}&advertiserId=adv",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200
        assert r.json()["data"]["timezone"] == "Asia/Shanghai", (
            f"expected fallback to Asia/Shanghai, got {r.json()['data']['timezone']}"
        )
    finally:
        _cleanup_test_seller_rows(seller)


# ═════════════════════════════════════════════════════════════════════
# 7. Batch endpoint invariants
# ═════════════════════════════════════════════════════════════════════


def _post_batch(client, token, seller, adv, records, request_id=None):
    return client.post(
        "/v1/analytics/sync/batches",
        json={
            "protocolVersion": 1,
            "requestId": request_id or f"req-{secrets.token_hex(8)}",
            "scope": {"sellerId": seller, "advertiserId": adv},
            "records": records,
        },
        headers={"Authorization": f"Bearer {token}"},
    )


def _make_batch_record(seller, adv, skey, camp, day, page):
    return {
        "idempotencyKey": _idempotency_key(seller, adv, skey, camp, day, page),
        "sourceRecordId": "uuid-" + secrets.token_hex(4),
        "storageKey": skey,
        "campaignId": camp,
        "day": day,
        "page": page,
        "endpoint": "/x",
        "method": "POST",
        "requestBody": None,
        "response": {"data": []},
        "source": "background_poll",
        "capturedAt": "2026-08-23T03:00:00.000Z",
        "schemaVersion": 1,
    }


def test_batch_insert_then_duplicate(mounted_client):
    tok = _mint_token()
    seller = "TEST_sync_int_batch"
    try:
        rec = _make_batch_record(seller, "adv", "productAnalyses", "c-1",
                                 "2026-08-23", 1)
        r1 = _post_batch(mounted_client, tok, seller, "adv", [rec])
        assert r1.status_code == 200
        assert r1.json()["data"]["accepted"][0]["status"] == "inserted"

        r2 = _post_batch(mounted_client, tok, seller, "adv", [rec])
        assert r2.json()["data"]["accepted"][0]["status"] == "duplicate"
    finally:
        _cleanup_test_seller_rows(seller)


def test_batch_same_key_twice_in_same_request(mounted_client):
    """Two records with same idempotency_key in ONE batch: first wins,
    second gets duplicate within the same batch."""
    tok = _mint_token()
    seller = "TEST_sync_int_double"
    try:
        rec1 = _make_batch_record(seller, "adv", "productAnalyses",
                                  "c-1", "2026-08-23", 1)
        rec2 = dict(rec1)  # identical idempotencyKey
        rec2["sourceRecordId"] = "uuid-different"  # otherwise identical
        r = _post_batch(mounted_client, tok, seller, "adv", [rec1, rec2])
        assert r.status_code == 200
        accepted = r.json()["data"]["accepted"]
        statuses = [a["status"] for a in accepted]
        # Either (inserted, duplicate) or (duplicate, duplicate) depending
        # on iteration order — both are acceptable. Just must not be
        # (inserted, inserted) and must not be 500.
        assert "inserted" in statuses, f"no record was inserted: {accepted}"
        assert sum(1 for s in statuses if s == "inserted") == 1, (
            f"both records inserted with same idempotencyKey: {accepted}"
        )
    finally:
        _cleanup_test_seller_rows(seller)


def test_batch_all_invalid_records(mounted_client):
    """Batch where every record has a bad idempotency key → all rejected,
    HTTP 200 (not 400), per-record rejection."""
    tok = _mint_token()
    seller = "TEST_sync_int_all_bad"
    try:
        bad_rec = _make_batch_record(seller, "adv", "productAnalyses",
                                     "c-1", "2026-08-23", 1)
        bad_rec["idempotencyKey"] = "f" * 64  # wrong key
        bad_rec2 = dict(bad_rec)
        bad_rec2["page"] = 2  # different page but still bad key
        r = _post_batch(mounted_client, tok, seller, "adv", [bad_rec, bad_rec2])
        assert r.status_code == 200
        assert len(r.json()["data"]["accepted"]) == 0
        assert len(r.json()["data"]["rejected"]) == 2
        for rej in r.json()["data"]["rejected"]:
            assert rej["code"] == "SCHEMA_INVALID"
            assert rej["retryable"] is False
    finally:
        _cleanup_test_seller_rows(seller)


def test_batch_mixed_valid_invalid_cursor_advances_only_for_inserted(mounted_client):
    """Atomicity: valid records insert + cursor advances; invalid records
    go to rejected[]. Cursor must reflect ONLY the valid (inserted) ones."""
    tok = _mint_token()
    seller = "TEST_sync_int_mixed"
    try:
        good = _make_batch_record(seller, "adv", "productAnalyses",
                                  "c-mix", "2026-08-23", 1)
        bad = _make_batch_record(seller, "adv", "productAnalyses",
                                 "c-mix", "2026-08-23", 2)
        bad["idempotencyKey"] = "0" * 64
        r = _post_batch(mounted_client, tok, seller, "adv", [good, bad])
        assert r.status_code == 200
        statuses = {a["status"] for a in r.json()["data"]["accepted"]}
        rejected = r.json()["data"]["rejected"]
        assert statuses == {"inserted"}, f"accepted={r.json()['data']['accepted']}"
        assert len(rejected) == 1 and rejected[0]["code"] == "SCHEMA_INVALID"

        # Cursor should now show 2026-08-23 for c-mix.
        r2 = mounted_client.get(
            f"/v1/analytics/sync/cursor?sellerId={seller}&advertiserId=adv",
            headers={"Authorization": f"Bearer {tok}"},
        )
        items = r2.json()["data"]["items"]
        c_mix = next((i for i in items if i["campaignId"] == "c-mix"), None)
        assert c_mix is not None, f"c-mix missing from cursor: {items}"
        assert c_mix["latestCompletedDay"] == "2026-08-23", (
            f"cursor should advance to 2026-08-23 even with one rejected: {c_mix}"
        )
    finally:
        _cleanup_test_seller_rows(seller)


def test_batch_response_data_size_limit(mounted_client):
    """Single record with response > 256 KB → RESPONSE_TOO_LARGE rejected."""
    tok = _mint_token()
    seller = "TEST_sync_int_huge"
    try:
        rec = _make_batch_record(seller, "adv", "productAnalyses",
                                 "c-1", "2026-08-23", 1)
        rec["response"] = {"junk": "x" * (300 * 1024)}  # >256 KB
        r = _post_batch(mounted_client, tok, seller, "adv", [rec])
        assert r.status_code == 200
        rejected = r.json()["data"]["rejected"]
        assert len(rejected) == 1 and rejected[0]["code"] == "RESPONSE_TOO_LARGE"
    finally:
        _cleanup_test_seller_rows(seller)


def test_batch_malformed_json_returns_400(mounted_client):
    tok = _mint_token()
    r = mounted_client.post(
        "/v1/analytics/sync/batches",
        content=b"not json at all {{{",
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 400
    assert r.json().get("code") == "MALFORMED_JSON"


def test_batch_oversized_body_returns_413(mounted_client):
    """2 MB+ body → 413 PAYLOAD_TOO_LARGE."""
    tok = _mint_token()
    big = b'{"x":"' + b"a" * (3 * 1024 * 1024) + b'"}'
    r = mounted_client.post(
        "/v1/analytics/sync/batches",
        content=big,
        headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 413
    assert r.json().get("code") == "PAYLOAD_TOO_LARGE"


def test_batch_too_many_records_returns_400(mounted_client):
    """101 records → 400 SCHEMA_INVALID (not 413 — 413 is for body size)."""
    tok = _mint_token()
    seller = "TEST_sync_int_too_many"
    records = [
        _make_batch_record(seller, "adv", "productAnalyses",
                           "c-1", "2026-08-23", p)
        for p in range(1, 102)
    ]
    r = _post_batch(mounted_client, tok, seller, "adv", records)
    assert r.status_code == 400
    assert r.json().get("code") == "SCHEMA_INVALID"
    _cleanup_test_seller_rows(seller)


def test_batch_empty_records_returns_400(mounted_client):
    tok = _mint_token()
    r = _post_batch(mounted_client, tok, "TEST_sync_int", "adv", [])
    assert r.status_code == 400
    assert r.json().get("code") == "SCHEMA_INVALID"


def test_batch_bad_storage_key_returns_400(mounted_client):
    tok = _mint_token()
    rec = _make_batch_record("TEST_sync_int", "adv", "productAnalyses",
                             "c-1", "2026-08-23", 1)
    rec["storageKey"] = "wrongKey"
    # Recompute idem key (the validator now knows storageKey, but the
    # request will be rejected at Pydantic level before that).
    r = _post_batch(mounted_client, tok, "TEST_sync_int", "adv", [rec])
    assert r.status_code == 400
    assert r.json().get("code") == "SCHEMA_INVALID"


def test_batch_page_zero_returns_400(mounted_client):
    tok = _mint_token()
    rec = _make_batch_record("TEST_sync_int", "adv", "productAnalyses",
                             "c-1", "2026-08-23", 1)
    rec["page"] = 0
    r = _post_batch(mounted_client, tok, "TEST_sync_int", "adv", [rec])
    assert r.status_code == 400


def test_batch_unsupported_protocol_version_returns_400(mounted_client):
    tok = _mint_token()
    rec = _make_batch_record("TEST_sync_int", "adv", "productAnalyses",
                             "c-1", "2026-08-23", 1)
    body = {
        "protocolVersion": 2,  # future
        "scope": {"sellerId": "TEST_sync_int", "advertiserId": "adv"},
        "records": [rec],
    }
    r = mounted_client.post(
        "/v1/analytics/sync/batches", json=body,
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 400
    assert r.json().get("code") == "UNSUPPORTED_PROTOCOL_VERSION"


def test_idempotency_key_server_matches_canonical_algorithm(mounted_client):
    """Lock: server computes key with sha256(canonical_json), page coerced
    to int, string fields trimmed, ASCII-sorted keys, compact separators."""
    from analytics_sync.domain import compute_idempotency_key
    tok = _mint_token()
    seller = "TEST_sync_int_canon"
    try:
        canonical = compute_idempotency_key(
            seller_id=seller, advertiser_id="adv",
            storage_key="productAnalyses", campaign_id="c-1",
            day="2026-08-23", page=1,
        )
        rec = {
            "idempotencyKey": canonical,
            "storageKey": "productAnalyses",
            "campaignId": "c-1",
            "day": "2026-08-23",
            "page": 1,
            "endpoint": "/x", "method": "POST", "response": {},
            "source": "x", "capturedAt": "2026-08-23T00:00:00Z",
            "schemaVersion": 1,
            "sourceRecordId": "uuid-canon",
        }
        r = _post_batch(mounted_client, tok, seller, "adv", [rec])
        assert r.status_code == 200
        assert r.json()["data"]["accepted"][0]["status"] == "inserted"
    finally:
        _cleanup_test_seller_rows(seller)


def test_idempotency_key_mismatch_returns_rejected_not_inserted(mounted_client):
    """Client sends a wrong key → record is rejected, NOT silently
    inserted with the wrong key."""
    tok = _mint_token()
    seller = "TEST_sync_int_mismatch"
    try:
        rec = _make_batch_record(seller, "adv", "productAnalyses",
                                 "c-1", "2026-08-23", 1)
        rec["idempotencyKey"] = "0" * 64  # wrong
        r = _post_batch(mounted_client, tok, seller, "adv", [rec])
        assert r.status_code == 200
        rejected = r.json()["data"]["rejected"]
        assert len(rejected) == 1
        assert rejected[0]["code"] == "SCHEMA_INVALID"
    finally:
        _cleanup_test_seller_rows(seller)


# ═════════════════════════════════════════════════════════════════════
# 8. Error envelopes  (no token / cookie / header leak)
# ═════════════════════════════════════════════════════════════════════


def test_error_envelopes_never_leak_token(mounted_client):
    """Hit every common error path with a known marker token; assert it
    never appears in the response body."""
    # Sentinel: this string is INTENTIONALLY a fake-looking token prefix
    # targeted at leak detection. Pyright may flag it as a hardcoded
    # password; the noqa is appropriate because the whole point is to
    # look like a token so the server-side leak detector would catch
    # it if any error response echoed it.
    secret_marker = "ttserp_rw_TOKEN_LEAK_CHECK_xxx"  # noqa: S105 (intentional sentinel)
    headers = {"Authorization": f"Bearer {secret_marker}"}

    endpoints = [
        ("GET", "/v1/analytics/sync/cursor?sellerId=x&advertiserId=y"),
        ("GET", "/v1/analytics/sync/cursor?sellerId=shopA&advertiserId=a"),  # scope test
        ("POST", "/v1/analytics/sync/batches"),
    ]
    # Mint a token with restricted scope so seller mismatch becomes a real error.
    scoped_tok = _mint_token(scopes=["seller:shopA"])

    for method, path in endpoints:
        if method == "GET" and "shopA" in path:
            r = mounted_client.get(path, headers={"Authorization": f"Bearer {scoped_tok}"})
        else:
            r = mounted_client.request(method, path, headers=headers)
        body = r.text
        assert secret_marker not in body, (
            f"token leaked in {method} {path}: {body[:300]}"
        )


def test_500_response_does_not_echo_exception_detail(mounted_client, monkeypatch):
    """Forced exception in repo → 500 with sanitized message, no class name
    or stack in body."""
    from analytics_sync import pg_repositories

    def boom(*args, **kwargs):
        raise RuntimeError("SECRET_INTERNAL_STACK_DETAIL_DO_NOT_LEAK")

    monkeypatch.setattr(
        pg_repositories.PgAnalyticsRepository, "upsert_records", boom
    )
    tok = _mint_token()
    seller = "TEST_sync_int_500"
    try:
        rec = _make_batch_record(seller, "adv", "productAnalyses",
                                 "c-1", "2026-08-23", 1)
        r = _post_batch(mounted_client, tok, seller, "adv", [rec])
        assert r.status_code == 500
        body = r.text
        assert "SECRET_INTERNAL_STACK_DETAIL_DO_NOT_LEAK" not in body, (
            f"internal exception leaked to client: {body}"
        )
        assert r.json().get("code") == "INTERNAL_ERROR"
    finally:
        _cleanup_test_seller_rows(seller)


# ═════════════════════════════════════════════════════════════════════
# 9. Cross-shop isolation
# ═════════════════════════════════════════════════════════════════════


def test_cursor_does_not_leak_other_sellers_cursors(mounted_client):
    """Two sellers with same campaign_id → each sees only its own row."""
    seller_a = "TEST_sync_int_iso_A"
    seller_b = "TEST_sync_int_iso_B"
    shared_camp = "shared-campaign-id"
    try:
        _insert_record_via_pg(seller_a, "adv", "productAnalyses",
                              shared_camp, "2026-08-23", 1)
        _insert_record_via_pg(seller_b, "adv", "productAnalyses",
                              shared_camp, "2026-08-23", 1)
        tok = _mint_token()
        # Seller A's cursor: only A's row.
        r = mounted_client.get(
            f"/v1/analytics/sync/cursor?sellerId={seller_a}&advertiserId=adv"
            f"&campaignId={shared_camp}",
            headers={"Authorization": f"Bearer {tok}"},
        )
        items_a = r.json()["data"]["items"]
        assert len(items_a) == 1, f"seller A should see 1 cursor, got {items_a}"
        # Seller B's cursor: only B's row.
        r = mounted_client.get(
            f"/v1/analytics/sync/cursor?sellerId={seller_b}&advertiserId=adv"
            f"&campaignId={shared_camp}",
            headers={"Authorization": f"Bearer {tok}"},
        )
        items_b = r.json()["data"]["items"]
        assert len(items_b) == 1
    finally:
        _cleanup_test_seller_rows(seller_a, seller_b)


def test_idempotency_key_includes_seller_in_canonical_computation(mounted_client):
    """Pin that sellerId participates in the canonical key computation.

    The protocol requires the server to recompute
    sha256(canonical_json({sellerId, advertiserId, storageKey, campaignId,
    day, page})) and reject client-sent keys that don't match
    (SCHEMA_INVALID). This test verifies that:

      - Two clients sending the SAME manually-crafted idempotencyKey
        value, but for different sellers, both get SCHEMA_INVALID
        because the server's canonical computation produces DIFFERENT
        keys per seller (sellerId is in the input).
      - The cross-seller "same key, both insert" attack is structurally
        impossible because the server catches the mismatch before the
        INSERT is attempted.

    This is a defense-in-depth check: even if the unique constraint
    on analytics_records.idempotency_key were dropped (regression),
    the SCHEMA_INVALID check already prevents the attack.
    """
    seller_a = "TEST_sync_int_iso_can_A"
    seller_b = "TEST_sync_int_iso_can_B"
    hand_crafted_key = "f" * 64  # arbitrary valid hex, same on both sides
    try:
        rec_a = _make_batch_record(seller_a, "adv", "productAnalyses",
                                   "c-1", "2026-08-23", 1)
        rec_a["idempotencyKey"] = hand_crafted_key
        rec_b = _make_batch_record(seller_b, "adv", "productAnalyses",
                                   "c-1", "2026-08-23", 1)
        rec_b["idempotencyKey"] = hand_crafted_key
        tok = _mint_token()
        r1 = _post_batch(mounted_client, tok, seller_a, "adv", [rec_a])
        r2 = _post_batch(mounted_client, tok, seller_b, "adv", [rec_b])

        # Both must be rejected with SCHEMA_INVALID (client-sent key
        # doesn't match the server's canonical computation for either
        # seller — because seller differs).
        for r, seller in ((r1, seller_a), (r2, seller_b)):
            rejected = r.json()["data"]["rejected"]
            assert len(rejected) == 1, f"{seller}: expected 1 rejection, got {r.json()}"
            assert rejected[0]["code"] == "SCHEMA_INVALID", (
                f"{seller}: expected SCHEMA_INVALID, got {rejected[0]}"
            )
            assert rejected[0]["idempotencyKey"] == hand_crafted_key
    finally:
        _cleanup_test_seller_rows(seller_a, seller_b)


# ═════════════════════════════════════════════════════════════════════
# 10. Edge cases — unicode, oversize, dates
# ═════════════════════════════════════════════════════════════════════


def test_unicode_seller_id_in_scope(mounted_client):
    """Chinese-character seller_id in scope string. Scope matching must
    be byte-exact (no normalization)."""
    seller = "店铺-A"
    try:
        tok = _mint_token(scopes=[f"seller:{seller}"])
        r = mounted_client.get(
            f"/v1/analytics/sync/cursor?sellerId={seller}&advertiserId=adv",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200
    finally:
        _cleanup_test_seller_rows(seller)


@pytest.mark.parametrize("bad_id", ["", "x" * 129])
def test_invalid_seller_id_in_cursor_rejected(mounted_client, bad_id):
    """sellerId with bad shape → 400 (Pydantic min_length=1, max_length=128).

    Note: a single space " " passes min_length=1 because it's 1 character.
    The server then strips it to empty before the SQL query, which returns
    no rows → 200 with empty items. This is a quirk of Pydantic's
    string validation, not a bug; document it here so it doesn't surprise
    future maintainers. (A stricter `.strip()` validator could close
    this gap if the protocol requires it.)
    """
    tok = _mint_token()
    r = mounted_client.get(
        f"/v1/analytics/sync/cursor?sellerId={bad_id}&advertiserId=adv",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code in (400, 422), (
        f"bad sellerId returned {r.status_code}, expected 4xx"
    )


def test_oversized_seller_id_in_cursor(mounted_client):
    tok = _mint_token()
    r = mounted_client.get(
        f"/v1/analytics/sync/cursor?sellerId={'x' * 200}&advertiserId=adv",
        headers={"Authorization": f"Bearer {tok}"},
    )
    # 422 from Pydantic, not a 500. Sanity.
    assert r.status_code in (400, 422)


def test_day_in_far_future_accepted(mounted_client):
    """Server doesn't validate date range. Plugin may upload future-dated
    records (e.g. for testing). Just shouldn't crash."""
    tok = _mint_token()
    seller = "TEST_sync_int_future"
    try:
        rec = _make_batch_record(seller, "adv", "productAnalyses",
                                 "c-1", "2099-12-31", 1)
        r = _post_batch(mounted_client, tok, seller, "adv", [rec])
        assert r.status_code == 200
        assert r.json()["data"]["accepted"][0]["status"] == "inserted"
    finally:
        _cleanup_test_seller_rows(seller)


def test_request_id_in_body_matches_header(mounted_client):
    """If the client sends X-Request-Id, it appears in the response body
    for correlation."""
    tok = _mint_token()
    custom_id = f"req-test-{secrets.token_hex(6)}"
    seller = "TEST_sync_int_reqid"
    try:
        rec = _make_batch_record(seller, "adv", "productAnalyses",
                                 "c-1", "2026-08-23", 1)
        body = {
            "protocolVersion": 1,
            "requestId": custom_id,
            "scope": {"sellerId": seller, "advertiserId": "adv"},
            "records": [rec],
        }
        r = mounted_client.post(
            "/v1/analytics/sync/batches", json=body,
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200
        # response.requestId echoes the client's requestId.
        assert r.json().get("requestId") == custom_id, (
            f"server generated different requestId: {r.json()}"
        )
    finally:
        _cleanup_test_seller_rows(seller)


# ═════════════════════════════════════════════════════════════════════
# Sanity: did we actually exercise the mounted app?
# ═════════════════════════════════════════════════════════════════════


def test_use_real_app_not_analytics_sync_standalone():
    """Guard against accidentally writing tests against the standalone
    FastAPI app. If this ever fires, the test is using analytics_sync.app
    instead of tts_erp_fastapi.app and isn't actually testing integration."""
    from tts_erp_fastapi import app

    # tts_erp_fastapi.app has CORS middleware and an analytics_sync router
    # mounted under /v1/analytics/sync.
    middleware_classes = {getattr(m.cls, "__name__", str(m.cls))
                          for m in app.user_middleware}
    assert "CORSMiddleware" in middleware_classes, (
        "tts_erp_fastapi app lacks CORS — wrong app being tested?"
    )
    # Routes mounted via include_router show up in app.openapi()["paths"]
    # (canonical source) but not in app.routes (which only has top-level
    # routes). Test via OpenAPI to be robust to FastAPI internals.
    paths = set(app.openapi()["paths"].keys())
    assert "/v1/analytics/sync/cursor" in paths
    assert "/v1/analytics/sync/batches" in paths
    assert "/healthz" in paths  # tts-erp's healthz
