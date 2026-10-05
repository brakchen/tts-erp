"""Safe structured events for the video publishing workflow."""

from __future__ import annotations

import json
import logging
from uuid import UUID

logger = logging.getLogger(__name__)


def emit_publish_event(
    event: str,
    *,
    task_id: UUID,
    attempt_id: int | None = None,
    artemis_session_id: UUID | None = None,
    attempt_kind: str | None = None,
    stage: str | None = None,
    outcome: str | None = None,
    device_serial: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """Emit only controlled identifiers/enums, never content or diagnostics."""
    payload: dict[str, str | int | None] = {
        "event": event,
        "task_id": str(task_id),
        "attempt_id": attempt_id,
        "artemis_session_id": (
            str(artemis_session_id) if artemis_session_id is not None else None
        ),
        "attempt_kind": attempt_kind,
        "stage": stage,
        "outcome": outcome,
        "device_serial_masked": (
            f"{device_serial[:4]}…{device_serial[-4:]}" if device_serial else None
        ),
        "duration_ms": max(0, int(duration_ms)) if duration_ms is not None else None,
    }
    try:
        logger.info(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    except Exception:  # noqa: BLE001 - observability must not alter business state
        return
