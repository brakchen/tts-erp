"""Transaction-owning implementation of order dump intake."""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tts_erp_v2.plugin.orders.intake._types import (
    DumpDomain,
    DumpIntakeOutcome,
    DumpIntakeRequest,
    IntakeFailureCode,
    IntakeTransactionConflict,
)
from tts_erp_v2.plugin.orders.parser import (
    parse_after_sales_response,
    parse_logistics_response,
    parse_order_detail_response,
    parse_order_history_response,
    parse_order_response,
    parse_statement_list_response,
    parse_statement_transaction_response,
)
from tts_erp_v2.plugin.orders.repository import record_dump_health

log = logging.getLogger("tts_erp_v2.plugin.orders.intake")


def _dispatch_dump(session: Session, request: DumpIntakeRequest) -> int:
    body = request.response_body
    if body is None:
        raise AssertionError("response body must be checked before dispatch")

    if request.domain is DumpDomain.ORDERS:
        return parse_order_response(
            session,
            shop_id=request.shop_id,
            response_body=body,
            captured_at=request.captured_at,
        )
    if request.domain is DumpDomain.ORDER_DETAILS:
        return parse_order_detail_response(
            session,
            shop_id=request.shop_id,
            response_body=body,
            captured_at=request.captured_at,
        )
    if request.domain is DumpDomain.ORDER_HISTORY:
        if not request.main_order_id:
            raise ValueError("mainOrderId is required for order_history domain")
        return parse_order_history_response(
            session,
            shop_id=request.shop_id,
            order_id=request.main_order_id,
            response_body=body,
            captured_at=request.captured_at,
        )
    if request.domain is DumpDomain.LOGISTICS:
        if not request.main_order_id:
            raise ValueError("mainOrderId is required for logistics domain")
        return parse_logistics_response(
            session,
            shop_id=request.shop_id,
            order_id=request.main_order_id,
            response_body=body,
            captured_at=request.captured_at,
        )
    if request.domain is DumpDomain.STATEMENTS:
        data = body.get("data") or {}
        if isinstance(data, dict) and "sku_record" in data:
            return parse_statement_transaction_response(
                session,
                shop_id=request.shop_id,
                response_body=body,
                captured_at=request.captured_at,
            )
        return parse_statement_list_response(
            session,
            shop_id=request.shop_id,
            response_body=body,
            captured_at=request.captured_at,
        )
    if request.domain is DumpDomain.AFTER_SALES:
        return parse_after_sales_response(
            session,
            shop_id=request.shop_id,
            response_body=body,
            captured_at=request.captured_at,
        )
    raise AssertionError(f"unhandled dump domain: {request.domain}")


def _record_health(
    session: Session,
    *,
    request: DumpIntakeRequest,
    rows_written: int,
    parse_error: str | None,
) -> None:
    record_dump_health(
        session,
        shop_id=request.shop_id,
        domain=request.domain.value,
        endpoint=request.endpoint,
        rows_written=rows_written,
        parse_error_class=parse_error,
        captured_at=request.captured_at,
    )


def _sanitize_parse_error(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}"
    return " ".join(message.split())[:500]


def _run_owned_transaction(
    session: Session,
    *,
    request: DumpIntakeRequest,
) -> DumpIntakeOutcome:
    if request.response_body is None:
        _record_health(
            session,
            request=request,
            rows_written=0,
            parse_error=IntakeFailureCode.EMPTY_RESPONSE_BODY.value,
        )
        session.commit()
        return DumpIntakeOutcome.rejected(
            code=IntakeFailureCode.EMPTY_RESPONSE_BODY,
            message="dump.response.body is null; plugin must not advance progress",
        )

    rows_written = 0
    parse_error: str | None = None
    try:
        with session.begin_nested():
            rows_written = _dispatch_dump(session, request)
    except SQLAlchemyError:
        raise
    except Exception as exc:
        parse_error = _sanitize_parse_error(exc)
        log.exception(
            "parse error for domain=%s shop_id=%s",
            request.domain.value,
            request.shop_id,
        )

    _record_health(
        session,
        request=request,
        rows_written=rows_written,
        parse_error=parse_error,
    )
    session.commit()

    if parse_error is not None:
        return DumpIntakeOutcome.rejected(
            code=IntakeFailureCode.PARSE_ERROR,
            message=parse_error,
        )
    return DumpIntakeOutcome.accepted(rows_written=rows_written)


def intake_dump(
    session: Session,
    *,
    request: DumpIntakeRequest,
) -> DumpIntakeOutcome:
    """Interpret and atomically persist one validated order-domain dump.

    Expected payload failures roll back business writes while committing one health
    record. Database failures roll back and propagate so the HTTP layer can return a
    retryable 5xx instead of misclassifying infrastructure failure as
    ``PARSE_ERROR``.
    """

    if session.in_transaction():
        raise IntakeTransactionConflict(
            "order dump intake requires a fresh session transaction"
        )

    try:
        return _run_owned_transaction(session, request=request)
    except SQLAlchemyError:
        log.exception(
            "database failure for domain=%s shop_id=%s",
            request.domain.value,
            request.shop_id,
        )
        try:
            session.rollback()
        except SQLAlchemyError:
            log.exception(
                "rollback failed for domain=%s shop_id=%s",
                request.domain.value,
                request.shop_id,
            )
        raise
