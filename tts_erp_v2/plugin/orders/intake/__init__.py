"""Deep module for validated order-domain dump intake.

HTTP callers provide semantic request values. The module owns domain dispatch,
payload interpretation, atomic persistence, health recording, and commit order.
"""

from __future__ import annotations

from tts_erp_v2.plugin.orders.intake._service import intake_dump
from tts_erp_v2.plugin.orders.intake._types import (
    DumpDomain,
    DumpIntakeOutcome,
    DumpIntakeRequest,
    IntakeFailure,
    IntakeFailureCode,
    IntakeStatus,
    IntakeTransactionConflict,
)

__all__ = [
    "DumpDomain",
    "DumpIntakeOutcome",
    "DumpIntakeRequest",
    "IntakeFailure",
    "IntakeFailureCode",
    "IntakeStatus",
    "IntakeTransactionConflict",
    "intake_dump",
]
