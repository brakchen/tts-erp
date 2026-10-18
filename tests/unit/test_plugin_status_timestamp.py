"""Decision timestamp round-trips must preserve the exact action boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from tts_erp_v2.db.models.plugin_status import StatusDecision
from tts_erp_v2.plugin.status.repository import decision_wire, wire_timestamp


@pytest.mark.parametrize("microsecond", [0, 1, 900_000, 999_999])
def test_wire_timestamp_preserves_the_stored_instant(microsecond: int) -> None:
    stored = datetime(2026, 10, 18, 4, 30, microsecond=microsecond, tzinfo=UTC)

    rendered = wire_timestamp(stored)

    assert rendered.endswith("Z")
    assert datetime.fromisoformat(rendered) == stored
    if microsecond == 0:
        assert rendered == "2026-10-18T04:30:00Z"


def test_wire_timestamp_normalizes_offset_without_losing_precision() -> None:
    stored = datetime(
        2026, 10, 18, 11, 30, 0, 900_001,
        tzinfo=timezone(timedelta(hours=7)),
    )

    assert wire_timestamp(stored) == "2026-10-18T04:30:00.900001Z"


@pytest.mark.parametrize("microsecond", [1, 900_000, 999_999])
def test_decision_wire_preserves_both_action_window_boundaries(
    microsecond: int,
) -> None:
    start = datetime(2026, 10, 18, 4, 30, microsecond=microsecond, tzinfo=UTC)
    end = start + timedelta(seconds=90)
    row = StatusDecision(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        revision=1,
        incident_id=UUID("00000000-0000-4000-8000-000000000002"),
        expected_binding_epoch=UUID("00000000-0000-4000-8000-000000000003"),
        action="hold",
        parameters={},
        not_before=start,
        expires_at=end,
    )

    wire = decision_wire(row).model_dump(mode="json", by_alias=True)

    assert datetime.fromisoformat(wire["notBefore"]) == start
    assert datetime.fromisoformat(wire["expiresAt"]) == end
