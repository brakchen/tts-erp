"""Contract tests for TikTok line-price normalization and persistence."""

from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime
from decimal import Decimal
import importlib.util
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.commerce import SalesOrderLinePriceObservation
from tts_erp_v2.jobs.tiktok.order_detail import detail_response_matches_request
from tts_erp_v2.jobs.tiktok.orders import incoming_order_is_stale
from tts_erp_v2.jobs.tiktok.order_prices import (
    DEFAULT_ONE_PER_LINE,
    GIFT,
    INVALID_CURRENCY,
    MISSING,
    NOT_GIFT,
    OBSERVED,
    PriceLineObservation,
    normalize_price_line,
    upsert_price_observation,
)

CAPTURED = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _backfill_module():
    path = Path(__file__).parents[2] / "scripts/oneoff_backfill_tiktok_line_prices.py"
    spec = importlib.util.spec_from_file_location("test_price_backfill", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def price_observation_schema(db_engine):
    """Apply this lane's migration inside the isolated test clone."""
    root = Path(__file__).parents[2]
    url = db_engine.url.render_as_string(hide_password=False)
    previous = {
        key: os.environ.get(key) for key in ("TTS_ERP_DB_URL", "TTS_ERP_DB_URL_TEST")
    }
    os.environ["TTS_ERP_DB_URL"] = url
    os.environ["TTS_ERP_DB_URL_TEST"] = url
    try:
        config = Config(str(root / "alembic.ini"))
        command.upgrade(config, "0054_tiktok_price_obs")
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _make_account(session, suffix: str):
    from tts_erp_v2.db.models import ChannelAccount, Credentials

    credential = Credentials(
        provider="tiktok",
        external_account_id=f"TEST_PRICE_CRED_{suffix}",
        ciphertext=b"\x00" * 32,
    )
    session.add(credential)
    session.flush()
    account = ChannelAccount(
        platform="tiktok",
        shop_id=f"TEST_PRICE_SHOP_{suffix}",
        credential_id=credential.id,
    )
    session.add(account)
    session.flush()
    return account


def _make_order_line_capture(session, account, suffix: str):
    from tts_erp_v2.db.models import RawRecord, SalesOrder, SalesOrderLine

    payload = {
        "order_id": f"TEST_PRICE_ORDER_{suffix}",
        "update_time": 1_791_000_000,
        "status": "AWAITING_SHIPMENT",
        "currency": "USD",
        "line_items": [],
    }
    raw = RawRecord(
        endpoint="/order/202309/orders/search",
        external_id=payload["order_id"],
        payload=payload,
        payload_hash="TEST_PRICE_RAW_HASH_" + suffix,
    )
    session.add(raw)
    session.flush()
    order = SalesOrder(
        shop_pk=account.id,
        order_id=payload["order_id"],
        status=payload["status"],
        currency=payload["currency"],
        order_modify_time=datetime.fromtimestamp(payload["update_time"], tz=UTC),
        raw_record_id=raw.id,
    )
    session.add(order)
    session.flush()
    line = SalesOrderLine(
        order_pk=order.id,
        external_line_id=f"TEST_PRICE_LINE_{suffix}",
        quantity=1,
        unit_price=Decimal("8"),
        currency="USD",
        raw_record_id=raw.id,
    )
    session.add(line)
    session.flush()
    return raw, order, line


def _line(**overrides: object) -> dict:
    value: dict = {
        "line_id": "TEST_LINE_1",
        "original_price": "10.00",
        "sale_price": "8.00",
        "currency": "USD",
        "quantity": 2,
        "is_gift": False,
        "display_status": "AWAITING_SHIPMENT",
    }
    value.update(overrides)
    return value


def _normalize(raw: dict, **overrides: object) -> PriceLineObservation:
    params = {
        "shop_pk": 11,
        "order_pk": 22,
        "external_line_id": "TEST_LINE_1",
        "raw_record_id": 33,
        "source_endpoint": "ORDER_SEARCH",
        "source_payload_hash": "raw-hash",
        "source_captured_at": CAPTURED,
        "source_order_version_at": datetime(2026, 10, 5, 11, 0, tzinfo=UTC),
        "parent_status": "AWAITING_SHIPMENT",
        "parent_currency": "USD",
        "spu_pk": 44,
    }
    params.update(overrides)
    return normalize_price_line(raw, **params)


def test_normalizer_accepts_zero_and_keeps_each_native_amount() -> None:
    observation = _normalize(_line(original_price="0", sale_price="0"))
    assert observation.original_price_native == Decimal("0")
    assert observation.paid_price_native == Decimal("0")
    assert observation.original_price_status == OBSERVED
    assert observation.paid_price_status == OBSERVED
    assert observation.effective_quantity == Decimal("2")


def test_normalizer_distinguishes_missing_quantity_from_invalid_present_quantity() -> None:
    observed = _normalize(_line(quantity=2))
    assert observed.quantity_status == OBSERVED
    omitted = _normalize({key: value for key, value in _line().items() if key != "quantity"})
    assert omitted.quantity_status == DEFAULT_ONE_PER_LINE
    assert omitted.effective_quantity == Decimal("1")
    explicit_null = _normalize(_line(quantity=None))
    assert explicit_null.quantity_status == "NULL"
    assert explicit_null.effective_quantity is None
    invalid = _normalize(_line(quantity=0))
    assert invalid.quantity_status == "INVALID_ZERO"
    assert invalid.effective_quantity is None


def test_normalizer_uses_strict_boolean_gift_mapping() -> None:
    assert _normalize(_line(is_gift=False)).gift_status == NOT_GIFT
    assert _normalize(_line(is_gift=True)).gift_status == GIFT
    assert _normalize(_line(is_gift="false")).gift_status == "UNKNOWN"
    assert _normalize(_line(is_gift=None)).gift_status == "UNKNOWN"


def test_normalizer_rejects_missing_or_conflicting_currency_without_guessing() -> None:
    missing = _normalize(_line(), parent_currency="USD")
    assert missing.original_price_status == OBSERVED
    assert missing.currency == "USD"
    conflicting = _normalize(_line(currency="SGD"), parent_currency="USD")
    assert conflicting.currency == "SGD"
    assert conflicting.original_price_status == INVALID_CURRENCY
    assert conflicting.paid_price_status == INVALID_CURRENCY
    assert conflicting.original_price_native == Decimal("10.00")


def test_semantic_identity_ignores_capture_raw_id_and_endpoint() -> None:
    first = _normalize(_line(), raw_record_id=33, source_endpoint="ORDER_SEARCH")
    retry = _normalize(
        _line(),
        raw_record_id=99,
        source_endpoint="ORDER_DETAIL",
        source_captured_at=CAPTURED.replace(hour=13),
    )
    assert first.semantic_observation_hash == retry.semantic_observation_hash
    assert first.source_payload_hash == retry.source_payload_hash


def test_detail_response_must_match_requested_order_id() -> None:
    assert detail_response_matches_request("TEST_ORDER", "TEST_ORDER")
    assert not detail_response_matches_request("TEST_ORDER", "OTHER_ORDER")


def test_order_version_guard_rejects_older_and_missing_incoming_versions() -> None:
    newer = datetime(2026, 10, 6, tzinfo=UTC)
    older = datetime(2026, 10, 5, tzinfo=UTC)
    assert incoming_order_is_stale(older, newer)
    assert incoming_order_is_stale(None, newer)
    assert not incoming_order_is_stale(newer, newer)
    assert not incoming_order_is_stale(newer, None)


def test_explicit_null_price_is_distinct_from_absent_price() -> None:
    explicit_null = _normalize(_line(sale_price=None))
    absent = _normalize({key: value for key, value in _line().items() if key != "sale_price"})
    assert explicit_null.paid_price_status == "NULL"
    assert absent.paid_price_status == MISSING


def test_new_missing_price_does_not_borrow_old_value() -> None:
    valid = _normalize(_line(sale_price="8.00"))
    missing = _normalize(
        {key: value for key, value in _line().items() if key != "sale_price"},
        source_order_version_at=datetime(2026, 10, 6, tzinfo=UTC),
    )
    assert valid.paid_price_native == Decimal("8.00")
    assert missing.paid_price_native is None
    assert missing.paid_price_status == MISSING
    assert valid.semantic_observation_hash != missing.semantic_observation_hash


def test_migration_creates_observation_contract(price_observation_schema, db_engine) -> None:
    inspector = inspect(db_engine)
    table = "sales_order_line_price_observations"
    assert table in inspector.get_table_names(schema="commerce")
    columns = {row["name"] for row in inspector.get_columns(table, schema="commerce")}
    assert {"shop_pk", "order_pk", "raw_record_id", "semantic_observation_hash", "original_price_native", "paid_price_native", "quantity_status", "gift_status"} <= columns
    constraints = inspector.get_unique_constraints(table, schema="commerce")
    assert any(c["name"] == "uq_solpo_line_semantic" for c in constraints)
    indexes = inspector.get_indexes(table, schema="commerce")
    assert {index["name"] for index in indexes} >= {"ix_solpo_line_version", "ix_solpo_spu_capture"}
    with db_engine.connect() as connection:
        trigger_names = set(
            connection.execute(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE tgrelid = 'commerce.sales_order_line_price_observations'::regclass "
                    "AND NOT tgisinternal"
                )
            ).scalars()
        )
    assert "trg_commerce_sales_order_line_price_observations_touch" in trigger_names


def test_duplicate_raw_captures_keep_first_observation_provenance(price_observation_schema, db_session) -> None:
    account = _make_account(db_session, "DUP")
    first_raw, order, line = _make_order_line_capture(db_session, account, "DUP")
    from tts_erp_v2.db.models import RawRecord

    second_raw = RawRecord(endpoint="/order/202309/orders", external_id=first_raw.external_id, payload=first_raw.payload, payload_hash="TEST_PRICE_RAW_HASH_DUP_2")
    db_session.add(second_raw)
    db_session.flush()
    first = _normalize(_line(line_id=line.external_line_id), shop_pk=account.id, order_pk=order.id, raw_record_id=first_raw.id, source_payload_hash="TEST_PAYLOAD_DUP", source_endpoint="ORDER_SEARCH", spu_pk=None)
    retry = _normalize(_line(line_id=line.external_line_id), shop_pk=account.id, order_pk=order.id, raw_record_id=second_raw.id, source_payload_hash="TEST_PAYLOAD_DUP", source_endpoint="ORDER_DETAIL", spu_pk=None)
    assert upsert_price_observation(db_session, first)
    assert not upsert_price_observation(db_session, retry)
    rows = db_session.execute(select(SalesOrderLinePriceObservation).where(SalesOrderLinePriceObservation.order_pk == order.id)).scalars().all()
    assert len(rows) == 1
    assert rows[0].raw_record_id == first_raw.id
    assert rows[0].source_endpoint == "ORDER_SEARCH"


def test_newer_missing_price_is_a_distinct_authoritative_observation(price_observation_schema, db_session) -> None:
    account = _make_account(db_session, "MISSING")
    first_raw, order, line = _make_order_line_capture(db_session, account, "MISSING")
    from tts_erp_v2.db.models import RawRecord

    second_raw = RawRecord(endpoint="/order/202309/orders/search", external_id=first_raw.external_id, payload=first_raw.payload, payload_hash="TEST_PRICE_RAW_HASH_MISSING_2")
    db_session.add(second_raw)
    db_session.flush()
    valid = _normalize(_line(line_id=line.external_line_id), shop_pk=account.id, order_pk=order.id, raw_record_id=first_raw.id, source_order_version_at=datetime(2026, 10, 5, tzinfo=UTC), spu_pk=None)
    missing = _normalize({key: value for key, value in _line(line_id=line.external_line_id).items() if key != "sale_price"}, shop_pk=account.id, order_pk=order.id, raw_record_id=second_raw.id, source_order_version_at=datetime(2026, 10, 6, tzinfo=UTC), spu_pk=None)
    assert upsert_price_observation(db_session, valid)
    assert upsert_price_observation(db_session, missing)
    rows = db_session.execute(select(SalesOrderLinePriceObservation).where(SalesOrderLinePriceObservation.order_pk == order.id).order_by(SalesOrderLinePriceObservation.source_order_version_at)).scalars().all()
    assert rows[0].paid_price_native == Decimal("8.00")
    assert rows[1].paid_price_native is None
    assert rows[1].paid_price_status == MISSING


class _SearchProxy:
    def __init__(self, payload: dict):
        self.payload = payload

    def __call__(self, method: str, path: str, *, body: dict | None = None, **kwargs):
        return {"code": 0, "data": {"orders": [self.payload]}}


class _DetailProxy:
    def __init__(self, payload: dict):
        self.payload = payload

    def __call__(self, method: str, path: str, *, body: dict | None = None, **kwargs):
        return {"code": 0, "data": {"order": self.payload}}


def _producer_payload(order_id: str, line_id: str) -> dict:
    return {
        "order_id": order_id,
        "order_status": "AWAITING_SHIPMENT",
        "currency": "USD",
        "update_time": 1_791_000_000,
        "line_items": [{"line_id": line_id, "product_id": "TEST_PRICE_PRODUCT", "original_price": "10.00", "sale_price": "8.00", "currency": "USD", "quantity": 1, "is_gift": False, "display_status": "AWAITING_SHIPMENT"}],
    }


def test_search_and_detail_use_same_observation_fields(price_observation_schema, db_session) -> None:
    from tts_erp_v2.db.models import SalesOrder, SyncIssue
    from tts_erp_v2.jobs.tiktok import order_detail, orders

    search_account = _make_account(db_session, "SEARCH")
    detail_account = _make_account(db_session, "DETAIL")
    search_result = orders.run(db_session, proxy_call=_SearchProxy(_producer_payload("TEST_PRICE_SEARCH_ORDER", "TEST_PRICE_SEARCH_LINE")), shop_id=search_account.shop_id)
    detail_result = order_detail.run(db_session, proxy_call=_DetailProxy(_producer_payload("TEST_PRICE_DETAIL_ORDER", "TEST_PRICE_DETAIL_LINE")), shop_id=detail_account.shop_id, order_ids=["TEST_PRICE_DETAIL_ORDER"])
    assert search_result.rows_failed == 0
    assert detail_result.rows_failed == 0
    rows = db_session.execute(select(SalesOrderLinePriceObservation).order_by(SalesOrderLinePriceObservation.order_pk)).scalars().all()
    assert len(rows) == 2
    assert {(row.original_price_native, row.paid_price_native, row.currency, row.gift_status) for row in rows} == {(Decimal("10.00"), Decimal("8.00"), "USD", "NOT_GIFT")}
    assert {row.source_endpoint for row in rows} == {"ORDER_SEARCH", "ORDER_DETAIL"}
    assert db_session.execute(select(SalesOrder)).scalars().all()
    assert not db_session.execute(select(SyncIssue)).scalars().all()


def test_detail_mismatched_response_does_not_persist_or_resolve_retry(price_observation_schema, db_session) -> None:
    from tts_erp_v2.db.models import RawRecord, SalesOrder, SyncIssue
    from tts_erp_v2.jobs.tiktok import order_detail

    account = _make_account(db_session, "MISMATCH")
    requested = "TEST_PRICE_REQUESTED_ORDER"
    db_session.add(SyncIssue(job_name=order_detail.JOB_NAME, issue_type="PARSE_ERROR", external_id=requested, details={"reason": "retry"}))
    db_session.flush()
    result = order_detail.run(db_session, proxy_call=_DetailProxy(_producer_payload("TEST_PRICE_OTHER_ORDER", "TEST_PRICE_OTHER_LINE")), shop_id=account.shop_id, order_ids=[requested])
    assert result.rows_failed == 1
    assert db_session.execute(
        select(SalesOrder).where(SalesOrder.order_id.in_([requested, "TEST_PRICE_OTHER_ORDER"]))
    ).scalars().all() == []
    assert db_session.execute(
        select(RawRecord).where(
            RawRecord.external_id.in_([requested, "TEST_PRICE_OTHER_ORDER"])
        )
    ).scalars().all() == []
    issues = db_session.execute(
        select(SyncIssue).where(SyncIssue.external_id == requested)
    ).scalars().all()
    assert {row.issue_type for row in issues} == {"PARSE_ERROR", "RESPONSE_ORDER_ID_MISMATCH"}
    assert next(row for row in issues if row.issue_type == "PARSE_ERROR").resolved_at is None


def test_backfill_dry_run_uses_lineage_and_writes_nothing(price_observation_schema, db_engine) -> None:
    from tts_erp_v2.db.models import RawRecord, SyncIssue

    with Session(db_engine) as session:
        account = _make_account(session, "DRYRUN")
        raw, order, line = _make_order_line_capture(session, account, "DRYRUN")
        raw.payload = {**raw.payload, "line_items": [{"line_id": line.external_line_id, "original_price": "10", "sale_price": "8", "currency": "USD", "is_gift": False}]}
        session.commit()
        before_observations = session.execute(
            select(SalesOrderLinePriceObservation).where(
                SalesOrderLinePriceObservation.order_pk == order.id
            )
        ).scalars().all()
        before_issues = session.execute(
            select(SyncIssue).where(SyncIssue.job_name == "tiktok.order_prices")
        ).scalars().all()
        counts = _backfill_module()._run(Namespace(shop_pk=account.id, dry_run=True, start_raw_id=0, end_raw_id=None, batch_size=100))
        assert counts["scanned"] == 1
        assert counts["inserted"] == 0
        assert session.execute(
            select(SalesOrderLinePriceObservation).where(
                SalesOrderLinePriceObservation.order_pk == order.id
            )
        ).scalars().all() == before_observations
        assert session.execute(
            select(SyncIssue).where(SyncIssue.job_name == "tiktok.order_prices")
        ).scalars().all() == before_issues


def test_backfill_rejects_raw_capture_shared_by_two_shops(price_observation_schema, db_engine) -> None:
    from tts_erp_v2.db.models import RawRecord, SalesOrder, SalesOrderLine

    with Session(db_engine) as session:
        first_account = _make_account(session, "SHARED_A")
        second_account = _make_account(session, "SHARED_B")
        raw = RawRecord(endpoint="/order/202309/orders/search", external_id="TEST_PRICE_SHARED_ORDER", payload={"order_id": "TEST_PRICE_SHARED_ORDER", "update_time": 1_791_000_000, "currency": "USD", "line_items": [{"line_id": "TEST_PRICE_SHARED_LINE", "sale_price": "8", "original_price": "10", "currency": "USD", "is_gift": False}]}, payload_hash="TEST_PRICE_SHARED_RAW")
        session.add(raw)
        session.flush()
        for account in (first_account, second_account):
            order = SalesOrder(shop_pk=account.id, order_id="TEST_PRICE_SHARED_ORDER", status="AWAITING_SHIPMENT", currency="USD", order_modify_time=datetime.fromtimestamp(1_791_000_000, tz=UTC), raw_record_id=raw.id)
            session.add(order)
            session.flush()
            session.add(SalesOrderLine(order_pk=order.id, external_line_id="TEST_PRICE_SHARED_LINE", quantity=1, unit_price=Decimal("8"), currency="USD", raw_record_id=raw.id))
        session.commit()
        counts = _backfill_module()._run(Namespace(shop_pk=first_account.id, dry_run=True, start_raw_id=0, end_raw_id=None, batch_size=100))
        assert counts["scanned"] == 1
        assert counts["unmatched"] == 1
        assert counts["inserted"] == 0


def test_backfill_confirm_replay_is_idempotent(price_observation_schema, db_engine) -> None:
    with Session(db_engine) as session:
        account = _make_account(session, "REPLAY")
        raw, order, line = _make_order_line_capture(session, account, "REPLAY")
        raw.payload = {
            **raw.payload,
            "line_items": [
                {
                    "line_id": line.external_line_id,
                    "original_price": "10",
                    "sale_price": "8",
                    "currency": "USD",
                    "is_gift": False,
                }
            ],
        }
        session.commit()
        module = _backfill_module()
        args = Namespace(
            shop_pk=account.id,
            dry_run=False,
            start_raw_id=0,
            end_raw_id=None,
            batch_size=100,
        )
        first = module._run(args)
        second = module._run(args)
        assert first["inserted"] == 1
        assert second["duplicate_noop"] == 1
        assert len(
            session.execute(
                select(SalesOrderLinePriceObservation).where(
                    SalesOrderLinePriceObservation.order_pk == order.id
                )
            ).scalars().all()
        ) == 1


def test_older_search_payload_preserves_current_mutable_rows_and_observation(
    price_observation_schema, db_session
) -> None:
    from tts_erp_v2.db.models import RawRecord, SalesOrder, SalesOrderLine
    from tts_erp_v2.jobs.tiktok import orders

    account = _make_account(db_session, "STALE")
    newer = _producer_payload("TEST_PRICE_STALE_ORDER", "TEST_PRICE_STALE_LINE")
    newer["update_time"] = 1_791_000_100
    newer["order_status"] = "DELIVERED"
    older = _producer_payload("TEST_PRICE_STALE_ORDER", "TEST_PRICE_STALE_LINE")
    older["update_time"] = 1_791_000_000
    older["order_status"] = "CANCELLED"
    older["currency"] = "SGD"
    older["line_items"][0]["quantity"] = 9
    orders.run(db_session, proxy_call=_SearchProxy(newer), shop_id=account.shop_id)
    orders.run(db_session, proxy_call=_SearchProxy(older), shop_id=account.shop_id)
    order = db_session.execute(
        select(SalesOrder).where(SalesOrder.shop_pk == account.id)
    ).scalar_one()
    line = db_session.execute(
        select(SalesOrderLine).where(SalesOrderLine.order_pk == order.id)
    ).scalar_one()
    assert order.status == "DELIVERED"
    assert order.currency == "USD"
    assert line.quantity == Decimal("1")
    observations = db_session.execute(
        select(SalesOrderLinePriceObservation).where(
            SalesOrderLinePriceObservation.order_pk == order.id
        )
    ).scalars().all()
    assert len(observations) == 1
    assert observations[0].paid_price_native == Decimal("8.00")
    assert len(
        db_session.execute(
            select(RawRecord).where(RawRecord.external_id == "TEST_PRICE_STALE_ORDER")
        ).scalars().all()
    ) == 2
