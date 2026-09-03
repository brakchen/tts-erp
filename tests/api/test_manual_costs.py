"""Manual-costs POST endpoint integration tests.

These hit the real (already-migrated) tts_erp_v2 schema with rolled-back
transactions (db_session fixture). We seed a channel product + account,
POST to /v2/reporting/manual-costs, and assert the row lands in
procurement.manual_product_costs with the expected fields.

Per Lane E spec:
- POST requires readwrite; readonly must get 403.
- Successful POST returns 201 + the new row.
- Currency must be ISO-4217 3 letters; non-ISO → 422.
- unit_cost must be > 0; ≤ 0 → 422.
- Unknown external_product_id → 404.
- A second submission for the same SPU closes the first row's valid_to
  (the "history is preserved" requirement).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


def _seed_channel_product(db_engine, external_id: str) -> int:
    """Seed a channel_account + channel_product pair; return the product id.

    Uses a dedicated real-commit session because the request handler
    reads via its own connection (the shared ``db_session`` fixture is
    savepoint-rolled-back and therefore invisible to the handler). The
    cleanup fixture wipes TEST_-prefixed rows at teardown.
    """
    from sqlalchemy.orm import Session

    with Session(db_engine) as sess:
        sess.execute(
            text(
                "INSERT INTO commerce.channel_accounts "
                "(platform, external_account_id, account_name, status) "
                "VALUES ('tiktok', 'TEST_acct_for_costs', 'TEST acct', 'active')"
            )
        )
        acct_id = sess.execute(
            text(
                "SELECT id FROM commerce.channel_accounts "
                "WHERE external_account_id = 'TEST_acct_for_costs'"
            )
        ).scalar()
        sess.execute(
            text(
                "INSERT INTO commerce.channel_products "
                "(channel_account_id, external_product_id, title, status) "
                "VALUES (:acct, :ext, 'TEST title', 'active')"
            ),
            {"acct": acct_id, "ext": external_id},
        )
        sess.commit()
        cp_id = sess.execute(
            text(
                "SELECT id FROM commerce.channel_products "
                "WHERE external_product_id = :ext"
            ),
            {"ext": external_id},
        ).scalar()
    return cp_id


def test_manual_costs_requires_readwrite(api_client, readonly_key):
    """readonly key cannot POST manual-costs."""
    r = api_client.post(
        "/v2/reporting/manual-costs",
        headers={"Authorization": f"Bearer {readonly_key}"},
        json={
            "channel_product_external_id": "TEST_mc_ro",
            "unit_cost": "1.00",
            "currency": "USD",
        },
    )
    assert r.status_code == 403, r.text


def test_manual_costs_rejects_unknown_spu(api_client, readwrite_key):
    """POST with an external_product_id that doesn't exist → 404."""
    r = api_client.post(
        "/v2/reporting/manual-costs",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_product_external_id": "TEST_definitely_not_a_real_spu",
            "unit_cost": "1.00",
            "currency": "USD",
        },
    )
    assert r.status_code == 404, r.text


def test_manual_costs_rejects_non_iso_currency(api_client, readwrite_key):
    """Currency must be 3-letter ISO; lowercase fails pattern validation."""
    r = api_client.post(
        "/v2/reporting/manual-costs",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_product_external_id": "TEST_currency_check",
            "unit_cost": "1.00",
            "currency": "us",  # 2 letters
        },
    )
    assert r.status_code == 422, r.text


def test_manual_costs_rejects_zero_or_negative(api_client, readwrite_key):
    """unit_cost must be > 0; zero fails pydantic Field(gt=0)."""
    r = api_client.post(
        "/v2/reporting/manual-costs",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_product_external_id": "TEST_zero_cost",
            "unit_cost": "0",
            "currency": "USD",
        },
    )
    assert r.status_code == 422, r.text


def test_manual_costs_happy_path_writes_row(
    api_client, readwrite_key, db_engine
):
    """Successful POST inserts into procurement.manual_product_costs."""
    cp_id = _seed_channel_product(db_engine, "TEST_mc_happy")
    r = api_client.post(
        "/v2/reporting/manual-costs",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_product_external_id": "TEST_mc_happy",
            "unit_cost": "12.34",
            "currency": "USD",
            "note": "first entry",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["channel_product_id"] == cp_id
    assert body["currency"] == "USD"
    assert body["note"] == "first entry"

    # Verify the row really is in the DB by reading back via a fresh
    # session (the handler commits via its own session; our shared
    # ``db_session`` fixture is savepoint-rolled-back).
    with Session(db_engine) as verify_sess:
        row = verify_sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict (see AGENTS.md "Critical Context")
            text(
                "SELECT unit_cost, currency, valid_to, note, created_by "
                "FROM procurement.manual_product_costs "
                "WHERE channel_product_id = :cp ORDER BY id DESC LIMIT 1"
            ),
            {"cp": cp_id},
        ).first()
    assert row is not None
    assert str(row.unit_cost).startswith("12.34")
    assert row.currency == "USD"
    assert row.valid_to is None  # currently effective
    assert row.note == "first entry"
    assert row.created_by.startswith("api_key:")


def test_manual_costs_second_submission_closes_first(
    api_client, readwrite_key, db_engine
):
    """Submitting twice for the same SPU: first row's valid_to gets set."""
    cp_id = _seed_channel_product(db_engine, "TEST_mc_history")

    r1 = api_client.post(
        "/v2/reporting/manual-costs",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_product_external_id": "TEST_mc_history",
            "unit_cost": "10.00",
            "currency": "USD",
        },
    )
    assert r1.status_code == 201

    r2 = api_client.post(
        "/v2/reporting/manual-costs",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_product_external_id": "TEST_mc_history",
            "unit_cost": "11.00",
            "currency": "USD",
        },
    )
    assert r2.status_code == 201

    with Session(db_engine) as verify_sess:
        rows = verify_sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict (see AGENTS.md "Critical Context")
            text(
                "SELECT id, unit_cost, valid_to FROM "
                "procurement.manual_product_costs "
                "WHERE channel_product_id = :cp ORDER BY id ASC"
            ),
            {"cp": cp_id},
        ).all()
    assert len(rows) == 2
    # First row (older): valid_to should now be set
    assert rows[0].valid_to is not None
    # Second row (newer): valid_to is NULL — this is the effective one
    assert rows[1].valid_to is None
    assert str(rows[1].unit_cost).startswith("11.00")
