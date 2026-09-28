"""Behaviour tests for the order dump intake module interface."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

from tts_erp_v2.plugin.orders.intake import (
    DumpDomain,
    DumpIntakeRequest,
    IntakeFailureCode,
    IntakeStatus,
    IntakeTransactionConflict,
    _service,
    intake_dump,
)

pytestmark = [pytest.mark.domain_sync, pytest.mark.layer_unit]


class _FakeSession:
    def __init__(
        self, *, active: bool = False, fail_commit: bool = False
    ) -> None:
        self.active = active
        self.fail_commit = fail_commit
        self.commits = 0
        self.rollbacks = 0
        self.savepoints = 0

    def in_transaction(self) -> bool:
        return self.active

    def begin_nested(self):
        self.savepoints += 1
        return nullcontext()

    def commit(self) -> None:
        if self.fail_commit:
            raise SQLAlchemyError("commit unavailable")
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _request(*, body: dict[str, Any] | None = None) -> DumpIntakeRequest:
    return DumpIntakeRequest(
        domain=DumpDomain.ORDERS,
        shop_id="TEST_shop",
        endpoint="/api/fulfillment/order/list",
        captured_at=datetime(2026, 9, 28, tzinfo=UTC),
        response_body={} if body is None else body,
    )


def test_intake_accepts_and_commits_business_and_health_once(monkeypatch) -> None:
    session = _FakeSession()
    health: list[tuple[int, str | None]] = []
    monkeypatch.setattr(_service, "_dispatch_dump", lambda *_args: 3)
    monkeypatch.setattr(
        _service,
        "_record_health",
        lambda _session, *, request, rows_written, parse_error: health.append(
            (rows_written, parse_error)
        ),
    )

    outcome = intake_dump(session, request=_request())  # type: ignore[arg-type]

    assert outcome.status is IntakeStatus.ACCEPTED
    assert outcome.rows_written == 3
    assert outcome.failure is None
    assert session.savepoints == 1
    assert session.commits == 1
    assert health == [(3, None)]


def test_intake_rejects_parse_error_but_commits_failure_health(monkeypatch) -> None:
    session = _FakeSession()
    health: list[tuple[int, str | None]] = []

    def _fail(*_args):
        raise ValueError("bad payload")

    monkeypatch.setattr(_service, "_dispatch_dump", _fail)
    monkeypatch.setattr(
        _service,
        "_record_health",
        lambda _session, *, request, rows_written, parse_error: health.append(
            (rows_written, parse_error)
        ),
    )

    outcome = intake_dump(session, request=_request())  # type: ignore[arg-type]

    assert outcome.status is IntakeStatus.REJECTED
    assert outcome.failure is not None
    assert outcome.failure.code is IntakeFailureCode.PARSE_ERROR
    assert "ValueError: bad payload" in outcome.failure.message
    assert session.commits == 1
    assert health == [(0, "ValueError: bad payload")]


def test_intake_empty_response_records_health_without_dispatch(monkeypatch) -> None:
    session = _FakeSession()
    health: list[tuple[int, str | None]] = []
    monkeypatch.setattr(
        _service,
        "_dispatch_dump",
        lambda *_args: pytest.fail("empty response must not dispatch"),
    )
    monkeypatch.setattr(
        _service,
        "_record_health",
        lambda _session, *, request, rows_written, parse_error: health.append(
            (rows_written, parse_error)
        ),
    )
    request = _request()
    request = DumpIntakeRequest(
        domain=request.domain,
        shop_id=request.shop_id,
        endpoint=request.endpoint,
        captured_at=request.captured_at,
        response_body=None,
    )

    outcome = intake_dump(session, request=request)  # type: ignore[arg-type]

    assert outcome.status is IntakeStatus.REJECTED
    assert outcome.failure is not None
    assert outcome.failure.code is IntakeFailureCode.EMPTY_RESPONSE_BODY
    assert session.savepoints == 0
    assert session.commits == 1
    assert health == [(0, "EMPTY_RESPONSE_BODY")]


def test_intake_database_failure_rolls_back_without_health_commit(monkeypatch) -> None:
    session = _FakeSession()
    monkeypatch.setattr(
        _service,
        "_dispatch_dump",
        lambda *_args: (_ for _ in ()).throw(SQLAlchemyError("db unavailable")),
    )
    monkeypatch.setattr(
        _service,
        "_record_health",
        lambda *_args, **_kwargs: pytest.fail("database failure must not fake health"),
    )

    with pytest.raises(SQLAlchemyError, match="db unavailable"):
        intake_dump(session, request=_request())  # type: ignore[arg-type]

    assert session.commits == 0
    assert session.rollbacks == 1


def test_intake_health_database_failure_rolls_back(monkeypatch) -> None:
    session = _FakeSession()
    monkeypatch.setattr(_service, "_dispatch_dump", lambda *_args: 1)
    monkeypatch.setattr(
        _service,
        "_record_health",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SQLAlchemyError("health unavailable")
        ),
    )

    with pytest.raises(SQLAlchemyError, match="health unavailable"):
        intake_dump(session, request=_request())  # type: ignore[arg-type]

    assert session.commits == 0
    assert session.rollbacks == 1


def test_intake_commit_database_failure_rolls_back(monkeypatch) -> None:
    session = _FakeSession(fail_commit=True)
    monkeypatch.setattr(_service, "_dispatch_dump", lambda *_args: 1)
    monkeypatch.setattr(_service, "_record_health", lambda *_args, **_kwargs: None)

    with pytest.raises(SQLAlchemyError, match="commit unavailable"):
        intake_dump(session, request=_request())  # type: ignore[arg-type]

    assert session.commits == 0
    assert session.rollbacks == 1


def test_intake_parse_error_is_sanitized_for_health_and_outcome(monkeypatch) -> None:
    session = _FakeSession()
    health: list[str | None] = []

    def _fail(*_args):
        raise ValueError("line one\n" + "x" * 600)

    monkeypatch.setattr(_service, "_dispatch_dump", _fail)
    monkeypatch.setattr(
        _service,
        "_record_health",
        lambda _session, *, request, rows_written, parse_error: health.append(
            parse_error
        ),
    )

    outcome = intake_dump(session, request=_request())  # type: ignore[arg-type]

    assert outcome.failure is not None
    assert "\n" not in outcome.failure.message
    assert len(outcome.failure.message) == 500
    assert health == [outcome.failure.message]


def test_intake_rejects_caller_owned_transaction() -> None:
    session = _FakeSession(active=True)
    with pytest.raises(IntakeTransactionConflict):
        intake_dump(session, request=_request())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("domain", "parser_name", "body", "main_order_id"),
    [
        (DumpDomain.ORDERS, "parse_order_response", {}, None),
        (
            DumpDomain.ORDER_DETAILS,
            "parse_order_detail_response",
            {"data": {"main_order": []}},
            None,
        ),
        (
            DumpDomain.ORDER_HISTORY,
            "parse_order_history_response",
            {"data": {"order_history": []}},
            "TEST_order",
        ),
        (
            DumpDomain.LOGISTICS,
            "parse_logistics_response",
            {"data": {"package_list": []}},
            "TEST_order",
        ),
        (
            DumpDomain.STATEMENTS,
            "parse_statement_list_response",
            {"data": {"statement_records": []}},
            None,
        ),
        (
            DumpDomain.STATEMENTS,
            "parse_statement_transaction_response",
            {"data": {"sku_record": {}}},
            None,
        ),
        (
            DumpDomain.AFTER_SALES,
            "parse_after_sales_response",
            {"data": {"cancellations": []}},
            None,
        ),
    ],
)
def test_dispatch_selects_each_domain_handler(
    monkeypatch,
    domain,
    parser_name,
    body,
    main_order_id,
) -> None:
    session = _FakeSession()
    calls: list[tuple[object, dict[str, Any]]] = []

    def _handler(actual_session, **kwargs):
        calls.append((actual_session, kwargs))
        return 7

    monkeypatch.setattr(_service, parser_name, _handler)
    request = DumpIntakeRequest(
        domain=domain,
        shop_id="TEST_shop",
        endpoint="/api/test",
        captured_at=datetime(2026, 9, 28, tzinfo=UTC),
        response_body=body,
        main_order_id=main_order_id,
    )

    assert _service._dispatch_dump(session, request) == 7  # type: ignore[arg-type]
    assert len(calls) == 1
    assert calls[0][0] is session
    if domain in {DumpDomain.LOGISTICS, DumpDomain.ORDER_HISTORY}:
        assert calls[0][1]["order_id"] == "TEST_order"


@pytest.mark.parametrize(
    "domain", [DumpDomain.LOGISTICS, DumpDomain.ORDER_HISTORY]
)
def test_dispatch_requires_main_order_id(domain) -> None:
    request = DumpIntakeRequest(
        domain=domain,
        shop_id="TEST_shop",
        endpoint="/api/test",
        captured_at=datetime(2026, 9, 28, tzinfo=UTC),
        response_body={},
    )
    with pytest.raises(ValueError, match="mainOrderId is required"):
        _service._dispatch_dump(_FakeSession(), request)  # type: ignore[arg-type]


def test_http_adapter_does_not_own_parser_or_transaction_seams() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[3]
        / "tts_erp_v2"
        / "api"
        / "v2"
        / "order_sync.py"
    ).read_text(encoding="utf-8")
    assert "plugin.orders.parser" not in source
    assert "record_dump_health" not in source
    assert "begin_nested" not in source
    assert "sess.commit" not in source
    assert "intake_dump" in source
