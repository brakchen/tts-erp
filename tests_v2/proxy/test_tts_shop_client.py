"""TDD tests for :mod:`tts_erp_v2.proxy.tts_shop.client`.

Covers the synchronous :class:`TiktokShopClient` and its tiny helpers
(:func:`_is_retryable_status`, :func:`_classify_response`,
:class:`TiktokCallResult`). The HTTP transport is mocked with a fake
``http.client.HTTPSConnection`` so we exercise every branch of
:meth:`TiktokShopClient._do` without hitting the network:

* happy path (GET + POST) returns ``TiktokCallResult`` with parsed JSON
* auth error → :class:`AuthenticationError` (401 / 403)
* rate limit → :class:`RateLimitedError` (429)
* non-retryable 4xx → :class:`UpstreamHttpError`
* retryable 5xx → :class:`TransientProxyError` after budget exhausted
* non-JSON body → ``ProxyError`` from :func:`_classify_response`
* JSON decode error in success branch → :class:`ProxyError`
* network error after retries → :class:`TransientProxyError`
* bad scheme → :class:`SigningError`
* missing host in URL → :class:`SigningError`
* constructor rejects empty credentials
* ``build_signed_url`` puts timestamp + sign in the query, sets scheme
* ``canonical_for`` exposes the canonical string used for signing

Mocking pattern mirrors ``test_tiktok_auth.py`` — substitute
``http.client.HTTPSConnection`` on the module under test.
"""

from __future__ import annotations

import dataclasses
import http.client
import json
import urllib.parse
from typing import Any
from unittest.mock import patch

import pytest

from tts_erp_v2.proxy import errors as proxy_errors
from tts_erp_v2.proxy.tts_shop import client as tts_client

pytestmark = [pytest.mark.domain_proxy, pytest.mark.layer_integration]

# Credential-shaped constants. Indexed via **kwargs so ruff's S105
# false-positive (literal "password-like" string passed as a keyword
# argument) only fires on the constant declaration, which has a
# single # noqa: S105. See test_tiktok_auth.py for the same idiom.
AK = "TEST_AK"
SK = "TEST_SK"
AT = "T_AT"
AT_BAD = "bad_at"
AT_T = "test_at"
SC = "T_SC"


# ─── HTTP transport fakes ───────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeConn:
    """Captures ``request`` calls and returns a scripted response."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes | None = None,
        raise_on_request: BaseException | None = None,
    ) -> None:
        self.status = status
        self.body = body if body is not None else json.dumps({"code": 0}).encode()
        self.raise_on_request = raise_on_request
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def request(self, method: str, path: str, body=None, headers=None) -> None:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "headers": headers or {},
            }
        )
        if self.raise_on_request is not None:
            raise self.raise_on_request

    def getresponse(self) -> _FakeResponse:
        return _FakeResponse(self.status, self.body)

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def client() -> tts_client.TiktokShopClient:
    """Client with no network calls."""
    return tts_client.TiktokShopClient(
        app_key=AK, app_secret=SK  # noqa: S106
    )


def _patch_https_connection(monkeypatch: pytest.MonkeyPatch, fake: _FakeConn) -> None:
    """Replace ``http.client.HTTPSConnection`` on the client module."""
    monkeypatch.setattr(
        tts_client.http.client,
        "HTTPSConnection",
        lambda *a, **kw: fake,
    )


# ─── Constructor ────────────────────────────────────────────────────


def test_constructor_rejects_empty_app_key() -> None:
    """Empty app_key is a configuration error — fail fast with SigningError."""
    with pytest.raises(proxy_errors.SigningError) as ei:
        tts_client.TiktokShopClient(app_key="", app_secret="x")  # noqa: S106
    assert "app_key" in str(ei.value)


def test_constructor_rejects_empty_app_secret() -> None:
    with pytest.raises(proxy_errors.SigningError):
        tts_client.TiktokShopClient(app_key="x", app_secret="")  # noqa: S106


def test_constructor_strips_trailing_slash_from_host() -> None:
    """``api_host`` is normalised so URL building doesn't add ``//``."""
    c = tts_client.TiktokShopClient(
        **{  # noqa: S106
            "app_key": "k",
            "app_secret": "s",
            "api_host": "https://x.example.com/",
        }
    )
    assert c.api_host == "https://x.example.com"


# ─── build_signed_url / canonical_for ───────────────────────────────


def test_build_signed_url_includes_timestamp_and_sign(
    client: tts_client.TiktokShopClient,
) -> None:
    """The signed URL must carry ``app_key``, ``timestamp``, ``sign`` and any
    ``extra_params`` — order is alphabetical (per ``urllib.parse.quote`` loop)."""
    url, ts = client.build_signed_url(
        path="/order/202309/orders/search",
        extra_params={"shop_cipher": "TEST_SC"},
    )
    assert ts.isdigit()
    parsed = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(parsed.query)
    assert q["app_key"] == [AK]
    assert q["shop_cipher"] == ["TEST_SC"]
    assert q["timestamp"] == [ts]
    # Signature is a 64-char lowercase hex digest.
    sig = q["sign"][0]
    assert len(sig) == 64 and all(c in "0123456789abcdef" for c in sig)


def test_canonical_for_is_deterministic(
    client: tts_client.TiktokShopClient,
) -> None:
    """``canonical_for`` uses ``timestamp="0"`` so the string is reproducible
    — useful for snapshot tests."""
    canon = client.canonical_for(
        path="/api/foo",
        extra_params={"shop_cipher": "X"},
        body='{"a":1}',
    )
    # Signature: secret + path + sorted-kv + body + secret
    expected_prefix = f"{SK}/api/fooapp_key{AK}shop_cipherXtimestamp0"
    assert canon.startswith(expected_prefix)
    assert canon.endswith('{"a":1}' + SK)


def test_canonical_for_without_body_omits_body(
    client: tts_client.TiktokShopClient,
) -> None:
    canon = client.canonical_for(path="/p")
    assert canon == f"{SK}/papp_key{AK}timestamp0{SK}"


# ─── Transport happy paths ──────────────────────────────────────────


def test_get_returns_parsed_json(
    monkeypatch: pytest.MonkeyPatch, client: tts_client.TiktokShopClient
) -> None:
    """GET success → TiktokCallResult with parsed JSON payload."""
    fake = _FakeConn(
        status=200,
        body=json.dumps(
            {"code": 0, "data": {"id": "O1"}, "message": "ok"}
        ).encode(),
    )
    _patch_https_connection(monkeypatch, fake)

    out = client.get(
        path="/order/202309/orders/O1",
        access_token=AT, extra_params={"shop_cipher": SC},  # noqa: S106
    )

    assert out.http_status == 200
    assert out.payload["code"] == 0
    assert out.payload["data"]["id"] == "O1"
    # GET has no body — verify the request shape.
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["headers"]["x-tts-access-token"] == AT
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["body"] == b""  # no body on GET
    # The path includes the signed query string.
    assert "shop_cipher=" in call["path"]
    assert "sign=" in call["path"]
    assert fake.closed


def test_post_sends_json_body(
    monkeypatch: pytest.MonkeyPatch, client: tts_client.TiktokShopClient
) -> None:
    """POST with body — body is the raw JSON string and is part of the
    HMAC canonical (per AGENTS.md §2.2)."""
    fake = _FakeConn(
        status=200,
        body=json.dumps({"code": 0, "data": {"id": "O2"}}).encode(),
    )
    _patch_https_connection(monkeypatch, fake)

    out = client.post(
        path="/order/202309/orders/search",
        body={"order_status": "UNSHIPPED", "page_size": 50},
        access_token=AT, extra_params={"shop_cipher": SC},  # noqa: S106
    )

    assert out.payload["data"]["id"] == "O2"
    call = fake.calls[0]
    assert call["method"] == "POST"
    # ensure_ascii=False → the body is the raw JSON, not url-encoded.
    sent_body = call["body"].decode("utf-8")
    assert json.loads(sent_body) == {
        "order_status": "UNSHIPPED",
        "page_size": 50,
    }


# ─── Error mapping ──────────────────────────────────────────────────


def test_post_401_raises_authentication_error(
    monkeypatch: pytest.MonkeyPatch, client: tts_client.TiktokShopClient
) -> None:
    fake = _FakeConn(
        status=401,
        body=json.dumps({"code": 401, "message": "token expired"}).encode(),
    )
    _patch_https_connection(monkeypatch, fake)

    with pytest.raises(proxy_errors.AuthenticationError) as ei:
        client.get(path="/x", access_token=AT_BAD)  # noqa: S106
    assert "401" in str(ei.value)
    assert "token expired" in str(ei.value)


def test_post_403_raises_authentication_error(
    monkeypatch: pytest.MonkeyPatch, client: tts_client.TiktokShopClient
) -> None:
    fake = _FakeConn(
        status=403,
        body=json.dumps({"code": 403, "message": "scope missing"}).encode(),
    )
    _patch_https_connection(monkeypatch, fake)

    with pytest.raises(proxy_errors.AuthenticationError) as ei:
        client.post(
            path="/x", body={"k": "v"}, access_token=AT_BAD  # noqa: S106
        )
    assert "scope missing" in str(ei.value)


def test_post_429_raises_rate_limited_error(
    monkeypatch: pytest.MonkeyPatch, client: tts_client.TiktokShopClient
) -> None:
    fake = _FakeConn(
        status=429,
        body=json.dumps({"code": 429, "message": "slow down"}).encode(),
    )
    _patch_https_connection(monkeypatch, fake)

    with pytest.raises(proxy_errors.RateLimitedError) as ei:
        client.get(path="/x", access_token=AT_T)  # noqa: S106
    assert "429" in str(ei.value)
    assert ei.value.body_preview is not None
    assert "slow down" in ei.value.body_preview


def test_post_400_raises_upstream_http_error(
    monkeypatch: pytest.MonkeyPatch, client: tts_client.TiktokShopClient
) -> None:
    fake = _FakeConn(
        status=400,
        body=json.dumps({"code": 36009004, "message": "PageSize is required"}).encode(),
    )
    _patch_https_connection(monkeypatch, fake)

    with pytest.raises(proxy_errors.UpstreamHttpError) as ei:
        client.post(
            path="/bad", body={}, access_token=AT_T  # noqa: S106
        )
    assert ei.value.status_code == 400
    assert ei.value.upstream_code == 36009004
    assert "PageSize" in str(ei.value)


def test_post_non_json_400_falls_back_to_envelope(
    monkeypatch: pytest.MonkeyPatch, client: tts_client.TiktokShopClient
) -> None:
    """When the upstream returns a non-JSON body, we synthesise an envelope
    with ``code=<http_status>`` so the caller still gets structured info."""
    fake = _FakeConn(
        status=400,
        body=b"<html>Bad Request</html>",
    )
    _patch_https_connection(monkeypatch, fake)

    with pytest.raises(proxy_errors.UpstreamHttpError) as ei:
        client.get(path="/html", access_token=AT_T)  # noqa: S106
    assert ei.value.status_code == 400
    assert "HTTP 400" in str(ei.value)


def test_post_5xx_retries_then_raises_transient(
    monkeypatch: pytest.MonkeyPatch, client: tts_client.TiktokShopClient
) -> None:
    """A retryable 5xx is retried up to ``max_retries + 1`` attempts; the
    final failure surfaces as :class:`TransientProxyError`."""
    fake = _FakeConn(status=503, body=b"overloaded")
    _patch_https_connection(monkeypatch, fake)

    # Patch the backoff sleep so the test is fast.
    with patch.object(tts_client.time, "sleep") as sleep_mock, pytest.raises(
        proxy_errors.TransientProxyError
    ) as ei:
        client.get(path="/flaky", access_token=AT_T)  # noqa: S106

    # 2 retries ⇒ 3 attempts, 2 backoff sleeps between them.
    assert len(fake.calls) == 3
    assert sleep_mock.call_count == 2
    assert "503" in str(ei.value)


def test_post_5xx_succeeds_after_one_retry(
    monkeypatch: pytest.MonkeyPatch, client: tts_client.TiktokShopClient
) -> None:
    """First attempt 503, second attempt 200 — eventual success."""
    state = {"calls": 0}

    class _SequenceConn(_FakeConn):
        def getresponse(self) -> _FakeResponse:
            state["calls"] += 1
            if state["calls"] == 1:
                return _FakeResponse(503, b"overloaded")
            return _FakeResponse(
                200, json.dumps({"code": 0, "data": {"ok": True}}).encode()
            )

    fake = _SequenceConn()
    _patch_https_connection(monkeypatch, fake)

    with patch.object(tts_client.time, "sleep"):
        out = client.get(path="/flaky", access_token=AT_T)  # noqa: S106

    assert state["calls"] == 2
    assert out.payload["data"]["ok"] is True


def test_post_json_decode_error_on_success_status(
    monkeypatch: pytest.MonkeyPatch, client: tts_client.TiktokShopClient
) -> None:
    """200 OK but the body isn't JSON → :class:`ProxyError`."""
    fake = _FakeConn(status=200, body=b"<not-json>")
    _patch_https_connection(monkeypatch, fake)

    with pytest.raises(proxy_errors.ProxyError) as ei:
        client.get(path="/x", access_token=AT_T)  # noqa: S106
    assert "JSON" in str(ei.value) or "decode" in str(ei.value).lower()


def test_network_error_after_retries_raises_transient(
    monkeypatch: pytest.MonkeyPatch, client: tts_client.TiktokShopClient
) -> None:
    """All attempts fail with a network error → :class:`TransientProxyError`."""
    fake = _FakeConn(raise_on_request=http.client.RemoteDisconnected("lost"))
    _patch_https_connection(monkeypatch, fake)

    with patch.object(tts_client.time, "sleep"), pytest.raises(
        proxy_errors.TransientProxyError
    ) as ei:
        client.get(path="/dead", access_token=AT_T)  # noqa: S106

    assert len(fake.calls) == 3  # max_retries=2 + 1 initial
    assert "network" in str(ei.value).lower()


def test_post_with_http_scheme_falls_back_to_http_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When api_host uses http://, the client uses HTTPConnection (not HTTPS)."""
    c = tts_client.TiktokShopClient(
        **{  # noqa: S106
            "app_key": "k",
            "app_secret": "s",
            "api_host": "http://internal.example.test",
        }
    )
    used: dict[str, Any] = {}

    class _SpyConn(_FakeConn):
        def request(self, method, path, body=None, headers=None) -> None:
            used["called"] = True
            super().request(method, path, body=body, headers=headers)

    fake = _SpyConn(status=200, body=b'{"code":0,"data":{}}')
    monkeypatch.setattr(
        tts_client.http.client, "HTTPConnection", lambda *a, **kw: fake
    )

    out = c.get(path="/x", access_token=AT_T)  # noqa: S106

    assert out.http_status == 200
    assert used["called"]


# ─── Helpers: _is_retryable_status / _classify_response ────────────


def test_is_retryable_status() -> None:
    assert tts_client._is_retryable_status(429) is True
    assert tts_client._is_retryable_status(500) is True
    assert tts_client._is_retryable_status(502) is True
    assert tts_client._is_retryable_status(599) is True
    # 4xx business errors are NOT retryable.
    assert tts_client._is_retryable_status(400) is False
    assert tts_client._is_retryable_status(401) is False
    assert tts_client._is_retryable_status(404) is False
    # 2xx / 3xx are not "retryable statuses" in the sense the client uses.
    assert tts_client._is_retryable_status(200) is False
    assert tts_client._is_retryable_status(301) is False


def test_classify_response_parses_json_dict() -> None:
    out = tts_client._classify_response(200, '{"code":0,"message":"ok"}')
    assert out == {"code": 0, "message": "ok"}


def test_classify_response_falls_back_on_non_dict_json() -> None:
    """A JSON array is still valid JSON but not the envelope shape — fall
    back to the synthetic envelope so downstream code gets a dict."""
    out = tts_client._classify_response(200, "[1,2,3]")
    assert out["code"] == 200
    assert "HTTP 200" in out["message"]
    assert "[1,2,3]" in out["_raw"]


def test_classify_response_falls_back_on_non_json() -> None:
    out = tts_client._classify_response(500, "<html>oops</html>")
    assert out["code"] == 500
    assert out["message"] == "HTTP 500"
    assert out["_raw"].startswith("<html>")


def test_classify_response_truncates_long_raw() -> None:
    long_body = "X" * 1000
    out = tts_client._classify_response(500, long_body)
    assert len(out["_raw"]) == 500


# ─── SigningError: scheme / host enforcement ────────────────────────


def test_post_refuses_non_http_scheme(client: tts_client.TiktokShopClient) -> None:
    """Forging an api_host like ``file://`` is refused at the signing layer."""
    c = tts_client.TiktokShopClient(
        **{  # noqa: S106
            "app_key": "k",
            "app_secret": "s",
            "api_host": "file:///etc/passwd",
        }
    )
    # The scheme check happens INSIDE _do, so the URL must reach _do first.
    # build_signed_url doesn't validate scheme — that's the explicit design
    # (single point of enforcement). Call post() and expect SigningError.
    with pytest.raises(proxy_errors.SigningError) as ei:
        c.post(path="/x", body={}, access_token=AT_T)  # noqa: S106
    assert "scheme" in str(ei.value).lower()


def test_post_refuses_missing_host(client: tts_client.TiktokShopClient) -> None:
    """A URL with no host (empty hostname) is rejected before any I/O."""
    c = tts_client.TiktokShopClient(
        **{  # noqa: S106
            "app_key": "k",
            "app_secret": "s",
            "api_host": "https:///path-only",
        }
    )
    with pytest.raises(proxy_errors.SigningError) as ei:
        c.post(path="/x", body={}, access_token=AT_T)  # noqa: S106
    assert "host" in str(ei.value).lower()


# ─── TiktokCallResult ──────────────────────────────────────────────


def test_tiktok_call_result_is_frozen() -> None:
    """The result dataclass is frozen — mutation raises ``AttributeError``
    or ``dataclasses.FrozenInstanceError`` (whichever the runtime emits)."""
    from tts_erp_v2.proxy.tts_shop.client import TiktokCallResult

    r = TiktokCallResult(payload={"code": 0}, http_status=200)
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        r.http_status = 201  # type: ignore[misc]
