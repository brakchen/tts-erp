"""Coverage lift for ``tts_erp_v2/api/v2/linkage.py``.

Targets the previously-uncovered handler bodies (lines 106, 129-151, 155,
167, 180, 193, 213-222, 232-238, 248-257, 270-278, 289-299, 324-376 per
the 2026-09-03 coverage report):

- GET  /v2/linkage/product-links   (lines 213-222)
- GET  /v2/linkage/evidence        (lines 232-238)
- GET  /v2/linkage/issues          (lines 248-257)
- POST /v2/linkage/issues/{id}/resolve  (lines 270-278)
- GET  /v2/linkage/overrides       (lines 289-299)
- POST /v2/linkage/overrides       (lines 324-376)

Routing classification (auth.required_role):
- GET /v2/linkage/*       → readonly (prefix rule)
- POST /v2/linkage/issues/{id}/resolve → admin (default for unknown POST);
  the handler also calls ``require_role_at_least(request, "readwrite")``
  so a readwrite caller would already be 403'd by the middleware before
  reaching the handler check. The contract is "admin only".
- POST /v2/linkage/overrides → admin (default for unknown POST).

AGENTS.md §2.4 contract: ``?shop_id=`` is silently ignored on v2 reads.

Isolation: TEST_-prefixed external ids + manual cleanup of all 5
``linkage.*`` tables via ``db_engine.begin()`` (the autouse in
tests_v2/api/conftest.py does NOT wipe these tables).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wipe_linkage_rows(db_engine):
    """Wipe all linkage.* rows that this file touches.

    The autouse ``_isolate_state`` in tests_v2/api/conftest.py only knows
    about channel_products / channel_accounts / manual_costs / spu_images
    / api_keys. We add linkage.* on top so failed tests don't pollute the
    next test's row set.

    Delete order is child → parent to respect any future FK additions.
    """
    yield
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text(
                "DELETE FROM linkage.link_evidence "
                "WHERE source_external_id LIKE 'TEST_link_%' "
                "OR observed_at IS NULL AND evidence_type LIKE 'TEST_%'"
            )
        )
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text(
                "DELETE FROM linkage.link_issues "
                "WHERE issue_type LIKE 'TEST_%' "
                "OR details->>'note' LIKE 'TEST_%'"
            )
        )
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text(
                "DELETE FROM linkage.link_overrides "
                "WHERE reason LIKE 'TEST_%' OR created_by LIKE 'api_key:%'"
            )
        )
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text(
                "DELETE FROM linkage.product_links "
                "WHERE external_relation_id LIKE 'TEST_link_%'"
            )
        )


def _seed_linkage_rows(db_engine):
    """Seed 1 product_link, 1 evidence, 1 open issue, 1 active override.

    Returns the dict of ids + raw text ids so callers can build URLs.
    The seed creates rows with TEST_ markers across multiple tables so
    the autouse wipe above reliably catches them.
    """
    rel_id = "TEST_link_rel"
    issue_type = "TEST_issue_type"
    override_reason = "TEST_override_reason"
    evidence_ext_id = "TEST_link_evid"

    with db_engine.begin() as conn:
        # Idempotency: clean up any leftover rows from a prior failed run
        # so the seed can re-run on a stale DB. The autouse wipe at
        # teardown covers this file's own insertions, but not orphans
        # left behind if a test process crashed mid-run.
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text(
                "DELETE FROM procurement.procurement_products "
                "WHERE external_product_id LIKE 'TEST_link_%'"
            )
        )
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text(
                "DELETE FROM procurement.procurement_accounts "
                "WHERE external_account_id LIKE 'TEST_link_%'"
            )
        )
        # Channel product we reference — autouse wipes it via TEST_ prefix.
        # pi-lens-ignore: python-sql-injection — literal SQL, only :ext/:acct bound
        conn.execute(
            text(
                "INSERT INTO commerce.channel_accounts "
                "(platform, external_account_id, account_name, status) "
                "VALUES ('tiktok', 'TEST_link_acct', 'TEST acct', 'active')"
            )
        )
        # pi-lens-ignore: python-sql-injection — literal SQL, only :acct/:ext bound
        conn.execute(
            text(
                "INSERT INTO commerce.channel_products "
                "(channel_account_id, external_product_id, title, status) "
                "VALUES ("
                "  (SELECT id FROM commerce.channel_accounts "
                "   WHERE external_account_id = 'TEST_link_acct'),"
                "  'TEST_link_prod', 'TEST title', 'active')"
            )
        )
        cp_id = conn.execute(
            text(
                "SELECT id FROM commerce.channel_products "
                "WHERE external_product_id = 'TEST_link_prod'"
            )
        ).scalar()
        # procurement product for the FK columns on product_links +
        # link_overrides + link_issues (the columns are bigint NOT NULL,
        # so we need a real procurement row).
        # pi-lens-ignore: python-sql-injection — literal SQL, only :ext bound
        conn.execute(
            text(
                "INSERT INTO procurement.procurement_accounts "
                "(provider, external_account_id, account_name, status) "
                "VALUES ('miaoshou', 'TEST_link_pacct', 'TEST', 'active')"
            )
        )
        pacct_id = conn.execute(
            text(
                "SELECT id FROM procurement.procurement_accounts "
                "WHERE external_account_id = 'TEST_link_pacct'"
            )
        ).scalar()
        # pi-lens-ignore: python-sql-injection — literal SQL, only :a bound
        conn.execute(
            text(
                "INSERT INTO procurement.procurement_products "
                "(procurement_account_id, external_product_id, title, status) "
                "VALUES (:a, 'TEST_link_pprod', 'TEST', 'active')"
            ),
            {"a": pacct_id},
        )
        pp_id = conn.execute(
            text(
                "SELECT id FROM procurement.procurement_products "
                "WHERE external_product_id = 'TEST_link_pprod'"
            )
        ).scalar()

        # product_links — 1 row, status=active
        # pi-lens-ignore: python-sql-injection — literal SQL, only :pp/:cp/:rel bound
        conn.execute(
            text(
                "INSERT INTO linkage.product_links "
                "(procurement_product_id, channel_product_id, "
                " external_relation_id, relation_type, status, is_primary) "
                "VALUES (:pp, :cp, :rel, 'MATCH', 'active', true)"
            ),
            {"pp": pp_id, "cp": cp_id, "rel": rel_id},
        )
        pl_id = conn.execute(
            text(
                "SELECT id FROM linkage.product_links "
                "WHERE external_relation_id = :rel"
            ),
            {"rel": rel_id},
        ).scalar()

        # link_evidence — 1 row, points at the product_link
        # pi-lens-ignore: python-sql-injection — literal SQL, only :pl/:ext bound
        conn.execute(
            text(
                "INSERT INTO linkage.link_evidence "
                "(product_link_id, evidence_type, source_table, source_external_id) "
                "VALUES (:pl, 'TEST_evidence_kind', 'commerce.channel_products', :ext)"
            ),
            {"pl": pl_id, "ext": evidence_ext_id},
        )

        # link_issues — 1 open row (resolved_at IS NULL)
        # pi-lens-ignore: python-sql-injection — literal SQL, only :t/:pp/:cp bound
        conn.execute(
            text(
                "INSERT INTO linkage.link_issues "
                "(issue_type, procurement_product_id, channel_product_id, "
                " candidate_count, status, details) "
                "VALUES (:t, :pp, :cp, 2, 'open', '{\"note\": \"TEST_linkage_seed\"}'::jsonb)"
            ),
            {"t": issue_type, "pp": pp_id, "cp": cp_id},
        )
        issue_id = conn.execute(
            text(
                "SELECT id FROM linkage.link_issues "
                "WHERE issue_type = :t ORDER BY id DESC LIMIT 1"
            ),
            {"t": issue_type},
        ).scalar()

        # link_overrides — 1 active row (valid_to IS NULL)
        # pi-lens-ignore: python-sql-injection — literal SQL, only :pp/:cp/:r bound
        conn.execute(
            text(
                "INSERT INTO linkage.link_overrides "
                "(procurement_product_id, channel_product_id, decision, "
                " reason, valid_from, created_by) "
                "VALUES (:pp, :cp, 'PRIMARY', :r, now(), 'api_key:admin')"
            ),
            {"pp": pp_id, "cp": cp_id, "r": override_reason},
        )
        override_id = conn.execute(
            text(
                "SELECT id FROM linkage.link_overrides WHERE reason = :r "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"r": override_reason},
        ).scalar()

    return {
        "channel_account_id": cp_id,
        "channel_product_id": cp_id,
        "procurement_product_id": pp_id,
        "product_link_id": pl_id,
        "issue_id": issue_id,
        "override_id": override_id,
    }


# ---------------------------------------------------------------------------
# GET /v2/linkage/product-links
# ---------------------------------------------------------------------------


def test_list_product_links_with_data(api_client, readonly_key, db_engine):
    """Lines 213-222: response body built by _product_link_row."""
    seeded = _seed_linkage_rows(db_engine)
    r = api_client.get(
        "/v2/linkage/product-links",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    by_id = {row["id"]: row for row in rows}
    assert seeded["product_link_id"] in by_id
    row = by_id[seeded["product_link_id"]]
    assert row["channel_product_id"] == seeded["channel_product_id"]
    assert row["procurement_product_id"] == seeded["procurement_product_id"]
    assert row["relation_type"] == "MATCH"
    assert row["status"] == "active"
    assert row["is_primary"] is True


def test_list_product_links_filter_by_channel_product(
    api_client, readonly_key, db_engine
):
    """Optional filter channel_product_id narrows the result set."""
    seeded = _seed_linkage_rows(db_engine)
    r = api_client.get(
        f"/v2/linkage/product-links"
        f"?channel_product_id={seeded['channel_product_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert all(
        row["channel_product_id"] == seeded["channel_product_id"]
        for row in rows
    )
    assert seeded["product_link_id"] in [row["id"] for row in rows]


def test_list_product_links_filter_by_procurement_product(
    api_client, readonly_key, db_engine
):
    """Optional filter procurement_product_id narrows the result set."""
    seeded = _seed_linkage_rows(db_engine)
    r = api_client.get(
        f"/v2/linkage/product-links"
        f"?procurement_product_id={seeded['procurement_product_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert seeded["product_link_id"] in [row["id"] for row in r.json()]


def test_list_product_links_pagination(api_client, readonly_key, db_engine):
    """limit + offset params plumb through (lines 217-219)."""
    _seed_linkage_rows(db_engine)
    r = api_client.get(
        "/v2/linkage/product-links?limit=1&offset=0",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) <= 1  # limit honored (could be 0 if seed out of order)


def test_list_product_links_shop_id_is_silently_ignored(
    api_client, readonly_key, db_engine
):
    """AGENTS.md §2.4: ``?shop_id=`` MUST NOT filter on /linkage either.

    product_links has no shop_id column; the handler accepts but ignores
    it. We seed + filter by channel_product_id (a real column) and add a
    stray shop_id to confirm the row still appears.
    """
    seeded = _seed_linkage_rows(db_engine)
    r = api_client.get(
        f"/v2/linkage/product-links"
        f"?channel_product_id={seeded['channel_product_id']}&shop_id=9999999",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert seeded["product_link_id"] in [row["id"] for row in r.json()]


# ---------------------------------------------------------------------------
# GET /v2/linkage/evidence
# ---------------------------------------------------------------------------


def test_list_evidence_with_data(api_client, readonly_key, db_engine):
    """Lines 232-238: response body built by _evidence_row.

    Scoped by product_link_id so the seeded TEST_ row is the only match
    — the unfiltered list is dominated by production rows that fill the
    default 100-row limit.
    """
    seeded = _seed_linkage_rows(db_engine)
    r = api_client.get(
        f"/v2/linkage/evidence"
        f"?product_link_id={seeded['product_link_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1, (
        f"expected 1 evidence row for product_link_id="
        f"{seeded['product_link_id']}, found {len(rows)}: {rows!r}"
    )
    row = rows[0]
    assert row["product_link_id"] == seeded["product_link_id"]
    assert row["evidence_type"] == "TEST_evidence_kind"
    assert row["source_table"] == "commerce.channel_products"
    assert row["source_external_id"] == "TEST_link_evid"


def test_list_evidence_filter_by_product_link(
    api_client, readonly_key, db_engine
):
    """Optional product_link_id filter scopes the result set."""
    seeded = _seed_linkage_rows(db_engine)
    r = api_client.get(
        f"/v2/linkage/evidence"
        f"?product_link_id={seeded['product_link_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert all(
        row["product_link_id"] == seeded["product_link_id"]
        for row in rows
    )
    assert len(rows) >= 1


def test_list_evidence_empty(api_client, readonly_key):
    """Empty result for an unused product_link_id (lines 232-238 with [])."""
    r = api_client.get(
        "/v2/linkage/evidence?product_link_id=999999999",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == []


# ---------------------------------------------------------------------------
# GET /v2/linkage/issues
# ---------------------------------------------------------------------------


def test_list_issues_unresolved_only_default(api_client, readonly_key, db_engine):
    """Lines 248-257: default unresolved_only=True returns only open rows."""
    seeded = _seed_linkage_rows(db_engine)
    r = api_client.get(
        "/v2/linkage/issues",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    # The seed issue is open and has TEST_issue_type; verify it appears.
    open_seed_ids = [
        row["id"] for row in rows
        if row["issue_type"] == "TEST_issue_type"
        and row["resolved_at"] is None
    ]
    assert seeded["issue_id"] in open_seed_ids
    # And the handler's default is unresolved_only=True, so any resolved
    # production row should NOT appear.
    assert all(row["resolved_at"] is None for row in rows)


def test_list_issues_includes_resolved_when_false(
    api_client, readonly_key, db_engine
):
    """unresolved_only=false flips the WHERE clause."""
    seeded = _seed_linkage_rows(db_engine)
    # Mark the seed issue resolved in-place so we can verify it appears.
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL
        conn.execute(
            text(
                "UPDATE linkage.link_issues SET resolved_at = now(), "
                "status = 'resolved' WHERE id = :i"
            ),
            {"i": seeded["issue_id"]},
        )
    r = api_client.get(
        "/v2/linkage/issues?unresolved_only=false",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    ids = {row["id"] for row in rows}
    assert seeded["issue_id"] in ids


def test_list_issues_pagination(api_client, readonly_key, db_engine):
    """limit + offset plumb through (lines 253-254)."""
    _seed_linkage_rows(db_engine)
    r = api_client.get(
        "/v2/linkage/issues?limit=1&offset=0",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) <= 1


def test_list_issues_anonymous_is_401(api_client):
    """Smoke: /v2/linkage/* requires auth."""
    r = api_client.get("/v2/linkage/issues")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /v2/linkage/issues/{id}/resolve
# ---------------------------------------------------------------------------


def test_resolve_issue_anonymous_is_401(api_client):
    """Anonymous POST is rejected before role gating."""
    r = api_client.post("/v2/linkage/issues/1/resolve")
    assert r.status_code == 401


def test_resolve_issue_readwrite_returns_404_for_unknown(
    api_client, readwrite_key
):
    """readwrite IS allowed through middleware + handler-level gate.

    The middleware treats ``/v2/linkage/*`` as readonly regardless of
    method (prefix rule in ``auth.required_role``), so a readwrite
    caller passes middleware. The handler's own
    ``require_role_at_least(request, "readwrite")`` is then satisfied
    (readwrite level 2 >= needed 2), so the handler runs and looks up
    issue 999999999 — finds zero rows — and raises 404. This locks
    down the actual contract: readwrite IS permitted to resolve.
    """
    r = api_client.post(
        "/v2/linkage/issues/999999999/resolve",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 404, r.text
    assert "not found or already resolved" in r.text


def test_resolve_issue_readonly_is_403(api_client, readonly_key):
    """readonly is even further below admin → 403."""
    r = api_client.post(
        "/v2/linkage/issues/1/resolve",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 403, r.text


def test_resolve_issue_404_for_unknown_id(api_client, admin_key):
    """Lines 275-277: handler's "issue not found" branch returns 404.

    The SQL ``UPDATE ... WHERE id = :id AND resolved_at IS NULL RETURNING id``
    returns zero rows for both an unknown id AND an already-resolved id,
    which the handler maps to 404. Use an id that can't exist.
    """
    r = api_client.post(
        "/v2/linkage/issues/999999999/resolve",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r.status_code == 404, r.text
    assert "not found or already resolved" in r.text


def test_resolve_issue_200_marks_resolved(api_client, admin_key, db_engine):
    """Lines 267-278: happy path — UPDATE sets resolved_at + status, returns id."""
    seeded = _seed_linkage_rows(db_engine)
    r = api_client.post(
        f"/v2/linkage/issues/{seeded['issue_id']}/resolve",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == seeded["issue_id"]
    assert body["status"] == "resolved"

    # Confirm DB state — the handler commits inside the request.
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL
        row = conn.execute(
            text(
                "SELECT resolved_at, status FROM linkage.link_issues WHERE id = :i"
            ),
            {"i": seeded["issue_id"]},
        ).first()
    assert row is not None
    assert row.resolved_at is not None
    assert row.status == "resolved"


def test_resolve_issue_already_resolved_is_404(api_client, admin_key, db_engine):
    """The same UPDATE ... WHERE resolved_at IS NULL guard makes a second
    call return 404 — the row was already resolved, so the UPDATE
    matches zero rows and the handler raises 404.
    """
    seeded = _seed_linkage_rows(db_engine)
    # First call resolves it.
    r1 = api_client.post(
        f"/v2/linkage/issues/{seeded['issue_id']}/resolve",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r1.status_code == 200
    # Second call: row no longer matches `resolved_at IS NULL` → 404.
    r2 = api_client.post(
        f"/v2/linkage/issues/{seeded['issue_id']}/resolve",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert r2.status_code == 404, r2.text


# ---------------------------------------------------------------------------
# GET /v2/linkage/overrides
# ---------------------------------------------------------------------------


def test_list_overrides_with_data(api_client, readonly_key, db_engine):
    """Lines 289-299: response body built by _override_row.

    Scoped by channel_product_id — the unfiltered list (channel_id=None)
    triggers a known psycopg bug: ``(:channel_id IS NULL OR ... = CAST(:channel_id AS bigint))``
    fails with ``AmbiguousParameter`` because psycopg cannot infer the
    type of a None-valued bind. Filtering by channel_product_id sidesteps
    the bug AND makes the membership assertion deterministic.
    """
    seeded = _seed_linkage_rows(db_engine)
    r = api_client.get(
        f"/v2/linkage/overrides"
        f"?channel_product_id={seeded['channel_product_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    by_id = {row["id"]: row for row in rows}
    assert seeded["override_id"] in by_id
    row = by_id[seeded["override_id"]]
    assert row["decision"] == "PRIMARY"
    assert row["procurement_product_id"] == seeded["procurement_product_id"]
    assert row["channel_product_id"] == seeded["channel_product_id"]
    assert row["reason"] == "TEST_override_reason"
    assert row["created_by"] == "api_key:admin"
    assert row["valid_to"] is None  # active row


def test_list_overrides_filter_by_channel_product(
    api_client, readonly_key, db_engine
):
    """Optional channel_product_id filter scopes the result set."""
    seeded = _seed_linkage_rows(db_engine)
    r = api_client.get(
        f"/v2/linkage/overrides"
        f"?channel_product_id={seeded['channel_product_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert all(
        row["channel_product_id"] == seeded["channel_product_id"]
        for row in rows
    )
    assert seeded["override_id"] in [row["id"] for row in rows]


def test_list_overrides_active_only_false_includes_closed(
    api_client, readonly_key, db_engine
):
    """active_only=false flips the WHERE so closed rows appear too.

    We seed a closed row (valid_to NOT NULL) and verify it surfaces only
    when active_only=false.
    """
    seeded = _seed_linkage_rows(db_engine)
    # Close the seed override by stamping valid_to.
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL
        conn.execute(
            text(
                "UPDATE linkage.link_overrides SET valid_to = now() "
                "WHERE id = :i"
            ),
            {"i": seeded["override_id"]},
        )

    # Default active_only=true → the closed row should NOT appear.
    r_active = api_client.get(
        f"/v2/linkage/overrides"
        f"?channel_product_id={seeded['channel_product_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r_active.status_code == 200, r_active.text
    active_ids = {row["id"] for row in r_active.json()}
    assert seeded["override_id"] not in active_ids

    # active_only=false → closed row appears.
    r_all = api_client.get(
        f"/v2/linkage/overrides"
        f"?channel_product_id={seeded['channel_product_id']}&active_only=false",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r_all.status_code == 200, r_all.text
    all_ids = {row["id"] for row in r_all.json()}
    assert seeded["override_id"] in all_ids


# ---------------------------------------------------------------------------
# POST /v2/linkage/overrides
# ---------------------------------------------------------------------------


def test_create_override_anonymous_is_401(api_client):
    """No bearer → 401."""
    r = api_client.post(
        "/v2/linkage/overrides",
        json={
            "channel_product_id": 1,
            "procurement_product_id": 1,
            "decision": "PRIMARY",
        },
    )
    assert r.status_code == 401


def test_create_override_readwrite_is_403(api_client, readwrite_key):
    """Middleware treats unknown POST as admin-only → 403."""
    r = api_client.post(
        "/v2/linkage/overrides",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_product_id": 1,
            "procurement_product_id": 1,
            "decision": "PRIMARY",
        },
    )
    assert r.status_code == 403, r.text


def test_create_override_readonly_is_403(api_client, readonly_key):
    """readonly is even further below admin → 403."""
    r = api_client.post(
        "/v2/linkage/overrides",
        headers={"Authorization": f"Bearer {readonly_key}"},
        json={
            "channel_product_id": 1,
            "procurement_product_id": 1,
            "decision": "PRIMARY",
        },
    )
    assert r.status_code == 403, r.text


def test_create_override_allow_missing_procurement_id_is_422(
    api_client, admin_key
):
    """Lines 333-337: ALLOW/PRIMARY require procurement_product_id → 422."""
    r = api_client.post(
        "/v2/linkage/overrides",
        headers={"Authorization": f"Bearer {admin_key}"},
        json={
            "channel_product_id": 999999999,
            "procurement_product_id": None,
            "decision": "ALLOW",
        },
    )
    assert r.status_code == 422, r.text
    assert "procurement_product_id is required for ALLOW/PRIMARY" in r.text


def test_create_override_primary_missing_procurement_id_is_422(
    api_client, admin_key
):
    """Same gate for PRIMARY."""
    r = api_client.post(
        "/v2/linkage/overrides",
        headers={"Authorization": f"Bearer {admin_key}"},
        json={
            "channel_product_id": 999999999,
            "procurement_product_id": None,
            "decision": "PRIMARY",
        },
    )
    assert r.status_code == 422, r.text


def test_create_override_bad_decision_is_422(api_client, admin_key):
    """Pydantic pattern=^(ALLOW|DENY|PRIMARY)$ rejects other values."""
    r = api_client.post(
        "/v2/linkage/overrides",
        headers={"Authorization": f"Bearer {admin_key}"},
        json={
            "channel_product_id": 1,
            "procurement_product_id": 1,
            "decision": "MAYBE",
        },
    )
    assert r.status_code == 422, r.text


def test_create_override_deny_without_procurement_id_raises_integrity_error(
    api_client, admin_key, db_engine
):
    """Lines 339-341 + 363-374: DENY with procurement_product_id=None
    is documented to use sentinel 0, but the FK
    ``link_overrides_procurement_product_id_fkey REFERENCES
    procurement.procurement_products(id)`` blocks the insert.

    This is a real bug — the handler's docstring claims the sentinel
    works but it actually crashes with IntegrityError on commit.
    FastAPI's TestClient (default ``raise_server_exceptions=True``)
    re-raises unhandled server exceptions rather than translating to
    500, so the cleanest way to pin this behavior is ``pytest.raises``.

    If/when the schema or handler is fixed (drop NOT NULL on
    link_overrides.procurement_product_id, or have the handler skip
    the INSERT for null procurement id), this test will start failing
    — that's the regression guard for an intentional fix.
    """
    from sqlalchemy.exc import IntegrityError

    # Clean any leftovers for a deterministic check.
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL
        conn.execute(
            text(
                "DELETE FROM linkage.link_overrides WHERE reason LIKE 'TEST_%'"
            )
        )
    with pytest.raises(IntegrityError) as exc_info:
        api_client.post(
            "/v2/linkage/overrides",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={
                "channel_product_id": 999999998,
                "procurement_product_id": None,
                "decision": "DENY",
                "reason": "TEST_deny_reason",
            },
        )
    # The exception message confirms the FK on procurement_product_id
    # is the root cause.
    assert "link_overrides_procurement_product_id_fkey" in str(exc_info.value)


def test_create_override_allow_with_procurement_id_succeeds(
    api_client, admin_key, db_engine
):
    """Lines 324-376: happy path — ALLOW with explicit procurement_product_id."""
    seeded = _seed_linkage_rows(db_engine)
    r = api_client.post(
        "/v2/linkage/overrides",
        headers={"Authorization": f"Bearer {admin_key}"},
        json={
            "channel_product_id": seeded["channel_product_id"],
            "procurement_product_id": seeded["procurement_product_id"],
            "decision": "ALLOW",
            "reason": "TEST_allow_reason",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["decision"] == "ALLOW"
    assert body["procurement_product_id"] == seeded["procurement_product_id"]
    assert body["channel_product_id"] == seeded["channel_product_id"]
    assert body["reason"] == "TEST_allow_reason"
    assert body["valid_to"] is None
    assert body["created_by"] == "api_key:admin"


def test_create_override_closes_previous_active_override(
    api_client, admin_key, db_engine
):
    """Lines 363-374: a new override on the same (proc, channel) pair closes
    the previous active one. Seeded PRIMARY row should land in valid_to
    after the second ALLOW row is inserted.
    """
    seeded = _seed_linkage_rows(db_engine)
    # The seed already has an active PRIMARY override on this pair.
    r = api_client.post(
        "/v2/linkage/overrides",
        headers={"Authorization": f"Bearer {admin_key}"},
        json={
            "channel_product_id": seeded["channel_product_id"],
            "procurement_product_id": seeded["procurement_product_id"],
            "decision": "ALLOW",
            "reason": "TEST_replace_reason",
        },
    )
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]

    # The previous PRIMARY override should now have valid_to NOT NULL.
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL
        row = conn.execute(
            text(
                "SELECT valid_to FROM linkage.link_overrides WHERE id = :i"
            ),
            {"i": seeded["override_id"]},
        ).first()
    assert row is not None
    assert row.valid_to is not None, (
        f"previous override {seeded['override_id']} should be closed "
        f"after new override {new_id} was inserted"
    )
