"""The report's existing cap must stop consumption, not just reject afterward."""

from __future__ import annotations

import asyncio

import pytest
from starlette.requests import ClientDisconnect, Request
from starlette.types import Message

from tts_erp_v2.api.v2.plugin_status import MAX_BODY_BYTES, _bounded_report_body

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_unit]


def _request_from_chunks(chunks: list[bytes]) -> tuple[Request, list[int]]:
    received: list[int] = []

    async def receive() -> Message:
        index = len(received)
        assert index < len(chunks), "TEST_unexpected_extra_receive"
        chunk = chunks[index]
        received.append(len(chunk))
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": index + 1 < len(chunks),
        }

    # A lying Content-Length must not turn into permission to buffer.
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v2/plugin/status/report",
            "headers": [(b"content-length", b"1")],
        },
        receive,
    )
    return request, received


@pytest.mark.parametrize(
    ("chunks", "expected_receives"),
    [
        ([b"x" * (MAX_BODY_BYTES + 1), b"TEST_unread_tail"], 1),
        ([b"x" * MAX_BODY_BYTES, b"y", b"TEST_unread_tail"], 2),
        ([b"", b"x" * (MAX_BODY_BYTES + 1), b"TEST_unread_tail"], 2),
    ],
    ids=["large-first-chunk", "split-overflow", "leading-empty"],
)
def test_oversized_report_stops_before_consuming_tail(
    chunks: list[bytes], expected_receives: int
) -> None:
    request, received = _request_from_chunks(chunks)
    assert asyncio.run(_bounded_report_body(request)) is None
    assert len(received) == expected_receives, (
        "oversize must not consume later ASGI chunks"
    )


@pytest.mark.parametrize(
    "chunks",
    [
        [b""],
        [b"{}"],
        [b"x" * MAX_BODY_BYTES],
        [b"x" * (MAX_BODY_BYTES // 2), b"y" * (MAX_BODY_BYTES // 2)],
    ],
    ids=["empty", "small", "exact-limit", "exact-split-limit"],
)
def test_body_within_limit_keeps_exact_bytes(chunks: list[bytes]) -> None:
    request, received = _request_from_chunks(chunks)
    assert asyncio.run(_bounded_report_body(request)) == b"".join(chunks)
    assert len(received) == len(chunks)


def test_transport_disconnect_preserves_existing_exception() -> None:
    async def disconnected() -> Message:
        return {"type": "http.disconnect"}

    request = Request({"type": "http", "headers": []}, disconnected)
    with pytest.raises(ClientDisconnect):
        asyncio.run(_bounded_report_body(request))
