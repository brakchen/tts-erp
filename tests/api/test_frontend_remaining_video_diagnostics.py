"""Publish diagnostics reuse the existing server redaction owner, never raw prompts."""

from __future__ import annotations

from uuid import UUID

import pytest

from tts_erp_v2.api.v2.video_publish import _attempt
from tts_erp_v2.db.models.publishing import VideoPublishAttempt

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_unit]


def _row(prompt: str | None) -> VideoPublishAttempt:
    return VideoPublishAttempt(
        sequence_no=1,
        kind="publish",
        status="succeeded",
        steps_count=0,
        artemis_session_id=UUID("00000000-0000-4000-8000-000000000001"),
        prompt_snapshot=prompt,
        artemis_output=None,
        retry_safe=False,
    )


def test_public_prompt_is_sanitized_without_mutating_stored_audit_fact():
    prompt = (
        "TEST public instructions\n"
        "Video https://TEST.invalid/video.mp4?X-Amz-Signature=TEST_SIGNATURE\n"
        "Authorization: Bearer TEST_BEARER\n"
        "Cookie: session=TEST_COOKIE\n"
        "password=TEST_PASSWORD\n"
    )
    row = _row(prompt)
    output = _attempt(row, diagnostics=True)["promptSnapshot"]
    assert output is not None and "TEST public instructions" in output
    assert "[REDACTED]" in output
    for secret in ("TEST_SIGNATURE", "TEST_BEARER", "TEST_COOKIE", "TEST_PASSWORD"):
        assert secret not in output
    assert row.prompt_snapshot == prompt


def test_prompt_uses_existing_bounded_diagnostic_text_policy():
    prompt = "TEST_" * 1000
    row = _row(prompt)
    output = _attempt(row, diagnostics=True)["promptSnapshot"]
    assert isinstance(output, str) and len(output) <= 2000
    assert row.prompt_snapshot == prompt


@pytest.mark.parametrize("prompt", [None, ""])
def test_nullable_and_empty_prompt_are_not_invented(prompt):
    assert _attempt(_row(prompt), diagnostics=True)["promptSnapshot"] == prompt


def test_default_task_reads_still_omit_diagnostic_material():
    output = _attempt(_row("TEST non-public prompt"))
    assert "promptSnapshot" not in output and "artemisOutput" not in output
