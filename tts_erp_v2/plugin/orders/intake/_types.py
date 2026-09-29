"""Domain types for the order dump intake module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class DumpDomain(StrEnum):
    ORDERS = "orders"
    LOGISTICS = "logistics"
    STATEMENTS = "statements"
    AFTER_SALES = "after_sales"
    ORDER_DETAILS = "order_details"
    ORDER_HISTORY = "order_history"


class IntakeStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class IntakeFailureCode(StrEnum):
    EMPTY_RESPONSE_BODY = "EMPTY_RESPONSE_BODY"
    PARSE_ERROR = "PARSE_ERROR"


class IntakeTransactionConflict(RuntimeError):
    """Raised when intake cannot own the caller's transaction."""


@dataclass(frozen=True, slots=True)
class DumpIntakeRequest:
    domain: DumpDomain
    shop_id: str
    endpoint: str
    captured_at: datetime
    response_body: dict[str, Any] | None
    main_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class IntakeFailure:
    code: IntakeFailureCode
    message: str


@dataclass(frozen=True, slots=True)
class DumpIntakeOutcome:
    status: IntakeStatus
    rows_written: int
    failure: IntakeFailure | None = None

    @classmethod
    def accepted(cls, *, rows_written: int) -> DumpIntakeOutcome:
        return cls(status=IntakeStatus.ACCEPTED, rows_written=rows_written)

    @classmethod
    def rejected(
        cls,
        *,
        code: IntakeFailureCode,
        message: str,
    ) -> DumpIntakeOutcome:
        return cls(
            status=IntakeStatus.REJECTED,
            rows_written=0,
            failure=IntakeFailure(code=code, message=message),
        )
