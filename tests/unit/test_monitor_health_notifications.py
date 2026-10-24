from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from sqlalchemy.orm import Session as SQLAlchemySession

from tts_erp_v2.plugin.monitor_health import notifications

pytestmark = pytest.mark.layer_unit
URL = "https://open.feishu.cn/open-apis/bot/v2/hook/TEST_fake"


@pytest.mark.parametrize(
    "url",
    [
        "http://open.feishu.cn/a",
        "https://evil.example/a",
        "https://open.feishu.cn.evil/a",
        "https://user@open.feishu.cn/a",
        "https://open.feishu.cn:444/a",
        "https://open.feishu.cn/a#secret",
    ],
)
def test_official_https_host_only(url):
    with pytest.raises(ValueError):
        notifications.send(url, ["TEST_fault"])


@pytest.mark.parametrize(
    "code,payload",
    [
        (200, {"code": 1}),
        (200, {}),
        (500, {"code": 0}),
        (302, {"code": 0}),
        (200, {"code": "0"}),
        (200, {"code": False}),
    ],
)
def test_transport_and_business_success_required(code, payload):
    transport = httpx.MockTransport(lambda request: httpx.Response(code, json=payload))
    with pytest.raises(RuntimeError, match="monitor health"):
        notifications.send(URL, ["TEST_fault"], transport=transport)


def test_success_and_no_url_in_failure():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 0})

    notifications.send(
        URL, ["TEST_shop scan:fault"], transport=httpx.MockTransport(handle)
    )
    assert len(requests) == 1
    assert b"TEST_shop" in requests[0].content
    transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError(URL))
    )
    with pytest.raises(RuntimeError) as caught:
        notifications.send(URL, ["TEST_fault"], transport=transport)
    assert URL not in str(caught.value)


def test_oversized_response_rejected():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x" * 4097)
    )
    with pytest.raises(RuntimeError):
        notifications.send(URL, [], transport=transport)


class Session:
    def __init__(self, item: SimpleNamespace | None, secret: SimpleNamespace | None):
        self.item, self.secret = item, secret

    def get(self, model, key):
        return self.item if key == "plugin.monitor_health.v1" else self.secret


def config(monkeypatch, **changes):
    item = SimpleNamespace(retired_at=None)
    secret = SimpleNamespace(retired_at=None, encrypted_value=b"TEST_ciphertext")
    revision = SimpleNamespace(
        payload={"feishuWebhook": "secret://monitor-health-feishu"}, rollout=[]
    )
    monkeypatch.setattr(
        notifications,
        "published_revision",
        lambda session, item: revision,
        raising=False,
    )
    monkeypatch.setattr(notifications, "decrypt", lambda value: URL, raising=False)
    for name, value in changes.items():
        setattr(revision, name, value)
    return Session(item, secret), revision


def test_published_revision_and_active_secret_resolution(monkeypatch):
    session, _ = config(monkeypatch)
    assert notifications.webhook(cast(SQLAlchemySession, session)) == URL


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "retired",
        "unpublished",
        "secret_missing",
        "secret_retired",
        "wrong_ref",
        "rollout",
    ],
)
def test_missing_or_inactive_config_fails_clearly(monkeypatch, failure):
    session, revision = config(monkeypatch)
    if failure == "missing":
        session.item = None
    elif failure == "retired":
        assert session.item is not None
        session.item.retired_at = True
    elif failure == "unpublished":
        monkeypatch.setattr(notifications, "published_revision", lambda *args: None)
    elif failure == "secret_missing":
        session.secret = None
    elif failure == "secret_retired":
        assert session.secret is not None
        session.secret.retired_at = True
    elif failure == "wrong_ref":
        revision.payload = {"feishuWebhook": "https://TEST_secret"}
    elif failure == "rollout":
        revision.rollout = ["TEST_retired"]
    with pytest.raises(RuntimeError, match="monitor health"):
        notifications.webhook(cast(SQLAlchemySession, session))


def test_info_logs_redact_only_feishu_bot_tokens(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="httpx")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"code": 0})
    )
    notifications.send(URL, ["TEST_notice"], transport=transport)
    logging.getLogger("httpx").info("unrelated TEST_log")
    assert "TEST_fake" not in caplog.text
    assert "unrelated TEST_log" in caplog.text
    assert "[monitor-health-webhook]" in caplog.text


@pytest.mark.parametrize("phase", ["connect", "request", "body", "trickle"])
def test_entire_sync_send_budget_cancels_stalls_and_cleans_up(monkeypatch, phase):
    import asyncio
    from time import monotonic

    closed = []
    entered = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            if phase == "body":
                await asyncio.sleep(1)
            for _ in range(30):
                await asyncio.sleep(0.01)
                yield b" "

        async def aclose(self):
            closed.append("body")

    class Transport(httpx.AsyncBaseTransport, httpx.BaseTransport):
        def handle_request(self, request):
            # Legacy phase-only sync sender cannot enforce the total deadline.
            import time

            time.sleep(0.06)
            return httpx.Response(200, json={"code": 0})

        async def handle_async_request(self, request):
            entered.append(phase)
            if phase in {"connect", "request"}:
                await asyncio.sleep(1)
            return httpx.Response(200, stream=Stream())

        async def aclose(self):
            closed.append("client")

    monkeypatch.setattr(notifications, "REQUEST_TIMEOUT_SECONDS", 0.02, raising=False)
    start = monotonic()
    with pytest.raises(RuntimeError, match="monitor health") as caught:
        notifications.send(URL, ["TEST_notice"], transport=Transport())
    assert monotonic() - start < 0.2
    assert entered == [phase]
    assert "client" in closed
    if phase in {"body", "trickle"}:
        assert "body" in closed
    assert "TEST_fake" not in str(caught.value)


def test_failure_info_logs_and_unrelated_urls_remain_safe(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="httpx")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"code": 1})
    )
    with pytest.raises(RuntimeError):
        notifications.send(URL, [], transport=transport)
    logging.getLogger("httpx").info("HTTP Request: GET https://example.com/TEST_public")
    assert "TEST_fake" not in caplog.text
    assert "https://example.com/TEST_public" in caplog.text


def test_running_event_loop_fails_clearly_without_orphan_coroutine():
    import asyncio

    async def call():
        with pytest.raises(RuntimeError, match="synchronous job context"):
            notifications.send(URL, [])

    asyncio.run(call())


def test_many_chunks_share_one_timeout_and_reuse_client(monkeypatch):
    import asyncio
    from time import monotonic

    calls, closed = [], []

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            calls.append(request)
            await asyncio.sleep(0.04)
            return httpx.Response(200, json={"code": 0})

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(notifications, "REQUEST_TIMEOUT_SECONDS", 0.065)
    started = monotonic()
    with pytest.raises(RuntimeError, match="monitor health") as error:
        notifications.send_many(
            URL, [["TEST_one"], ["TEST_two"], ["TEST_three"]], transport=Transport()
        )
    assert monotonic() - started < 0.15
    assert getattr(error.value, "accepted_batches", None) == 1
    assert len(calls) == 2 and len(closed) == 1


def test_many_chunks_partial_business_failure_reports_only_accepted_prefix(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="httpx")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"code": 0 if len(calls) == 1 else 1})

    with pytest.raises(RuntimeError, match="monitor health") as error:
        notifications.send_many(
            URL,
            [["TEST_one"], ["TEST_two"], ["TEST_three"]],
            transport=httpx.MockTransport(handle),
        )
    assert getattr(error.value, "accepted_batches", None) == 1
    assert len(calls) == 2
    assert "TEST_fake" not in str(error.value) + caplog.text


@pytest.mark.parametrize("phase", ["request", "body", "response_close", "client_close"])
@pytest.mark.parametrize("prior_accepted", [0, 1])
def test_slow_cooperative_cleanup_bounded_by_entire_shared_budget(
    monkeypatch, phase, prior_accepted
):
    import asyncio
    from time import monotonic

    cancelled = []
    calls = []
    close_started = []

    async def slow_close(owner):
        close_started.append(owner)
        try:
            await asyncio.sleep(0.3)  # Five times the ENTIRE dispatch budget.
        except asyncio.CancelledError:
            cancelled.append(owner)
            raise

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            if phase == "body":
                await asyncio.sleep(1)
            yield b'{"code":0}'

        async def aclose(self):
            await slow_close("response")

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            calls.append(request)
            if len(calls) <= prior_accepted:
                return httpx.Response(200, json={"code": 0})
            if phase == "request":
                await asyncio.sleep(1)
            if phase in {"body", "response_close"}:
                return httpx.Response(200, stream=Stream())
            return httpx.Response(200, json={"code": 0})

        async def aclose(self):
            await slow_close("client")

    monkeypatch.setattr(notifications, "REQUEST_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(notifications, "NOTIFICATION_BUDGET_SECONDS", 0.06)
    started = monotonic()
    with pytest.raises(notifications.DeliveryError) as caught:
        notifications.send_many(
            URL,
            [[f"TEST_{i}"] for i in range(prior_accepted + 1)],
            transport=Transport(),
        )
    elapsed = monotonic() - started
    assert elapsed < 0.1, f"whole sync dispatch exceeded 0.06s budget: {elapsed:.3f}s"
    assert "client" in cancelled
    if phase in {"body", "response_close"}:
        assert "response" in cancelled  # Both ordinary and unwind cleanup are bounded.
    assert len(calls) == prior_accepted + 1
    expected = prior_accepted + (1 if phase == "client_close" else 0)
    assert caught.value.accepted_batches == expected
    assert "TEST_fake" not in str(caught.value)
    assert "client" in close_started
