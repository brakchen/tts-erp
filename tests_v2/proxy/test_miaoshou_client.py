"""TDD tests for :mod:`tts_erp_v2.proxy.miaoshou.client`.

Covers the open-platform and ERP Miaoshou clients (MD5 and HMAC-SHA256
signing respectively), their helpers (:func:`_assert_safe_url`,
:func:`safe_http_post_json`, :class:`EnvConfig`,
:class:`MiaoshouApiResponse`), and the public surface (constructor
variants, ``from_env`` factory, context-manager protocol, single
call envelopes).

HTTP transport is mocked via :func:`safe_http_post_json` (the same seam
``test_miaoshou_erp_sign_headers.py`` uses) so we exercise every branch
of the call layer without hitting the network. Where :class:`MiaoshouClient._call`
takes a real :func:`safe_http_post_json` path, we monkeypatch it.

Tests cover:

* :class:`MiaoshouApiResponse` ``ok`` / ``raise_for_status``
* :class:`MiaoshouApiError` carries ``code`` / ``message`` / ``data``
* :func:`_assert_safe_url` refuses non-http(s) and missing host
* :func:`safe_http_post_json` success + HTTP-error → :class:`MiaoshouApiError`
* :class:`EnvConfig.from_name` prod / test / invalid
* :class:`MiaoshouClient` init / ``from_env`` / context-manager
* :class:`MiaoshouClient._call` happy path: envelope shape, URL build,
  upstream ``code=200`` parsed into :class:`MiaoshouApiResponse`
* :class:`MiaoshouClient._call` non-200 upstream code raises
* :class:`MiaoshouClient._call` non-JSON upstream body raises
* :class:`MiaoshouClient._call` int-code fallback for non-rate codes
* :class:`MiaoshouClient._call` ``absolute=True`` path skip base URL
* :class:`MiaoshouErpClient` init / ``from_env``
* :class:`MiaoshouErpClient._call_erp` happy path: envelope sign header
* :class:`MiaoshouErpClient._call_erp`` ``result="fail"`` raises
* :class:`MiaoshouErpClient._call_erp` non-JSON body raises
* :class:`MiaoshouErpClient._call_erp` urllib HTTPError w/ JSON body
* :class:`MiaoshouErpClient._call_erp` urllib HTTPError w/o JSON body
* :class:`MiaoshouErpClient._call_erp` query string appended
"""

from __future__ import annotations

import http.client
import io
import json
import urllib.error
from typing import Any, cast

import pytest

from tts_erp_v2.proxy import errors as proxy_errors
from tts_erp_v2.proxy.miaoshou import client as ms_client
from tts_erp_v2.proxy.miaoshou.client import (
    PROD_BASE,
    PROD_USER_OPEN_PREFIX,
    TEST_BASE,
    TEST_USER_OPEN_PREFIX,
    EnvConfig,
    MiaoshouApiError,
    MiaoshouApiResponse,
    MiaoshouClient,
    MiaoshouErpClient,
    safe_http_post_json,
)

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


# Credential-shaped constants — see test_tiktok_auth.py for the same idiom.
LIC = "TEST_LICENSE"
SEC = "TEST_SECRET"  # noqa: S105


# ─── MiaoshouApiResponse ────────────────────────────────────────────


def test_miaoshou_api_response_ok_when_code_200() -> None:
    """Open-platform success code is 200 (not 0)."""
    r = MiaoshouApiResponse(code=200, message="ok", data={"x": 1})
    assert r.ok is True
    # raise_for_status returns self for chaining.
    assert r.raise_for_status() is r


def test_miaoshou_api_response_not_ok_when_code_not_200() -> None:
    r = MiaoshouApiResponse(code=400, message="bad", data=None)
    assert r.ok is False
    with pytest.raises(MiaoshouApiError) as ei:
        r.raise_for_status()
    assert ei.value.code == 400
    assert ei.value.message == "bad"


def test_miaoshou_api_error_str_format() -> None:
    err = MiaoshouApiError(code=500, message="boom", data=None)
    assert str(err) == "[code=500] boom"
    assert err.code == 500
    assert err.data is None


# ─── _assert_safe_url / safe_http_post_json ────────────────────────


def test_assert_safe_url_rejects_non_http_scheme() -> None:
    with pytest.raises(proxy_errors.SigningError) as ei:
        ms_client._assert_safe_url("file:///etc/passwd")
    assert "scheme" in str(ei.value).lower()


def test_assert_safe_url_rejects_missing_host() -> None:
    with pytest.raises(proxy_errors.SigningError) as ei:
        ms_client._assert_safe_url("https:///path-only")
    assert "host" in str(ei.value).lower()


def test_assert_safe_url_returns_parsed_for_valid_https() -> None:
    p = ms_client._assert_safe_url("https://api.example.com/v1/foo")
    assert p.scheme == "https"
    assert p.hostname == "api.example.com"
    assert p.path == "/v1/foo"


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeConn:
    def __init__(self, *, status: int = 200, body: bytes = b"") -> None:
        self.status = status
        self.body = body
        self.calls: list[dict[str, Any]] = []

    def request(self, method, path, body=None, headers=None) -> None:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "headers": headers or {},
            }
        )

    def getresponse(self) -> _FakeResponse:
        return _FakeResponse(self.status, self.body)

    def close(self) -> None:
        pass


def test_safe_http_post_json_returns_body_on_2xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2xx upstream → returns the response body string."""
    fake = _FakeConn(status=200, body=b'{"code":200,"data":[]}')
    monkeypatch.setattr(
        ms_client.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )
    out = safe_http_post_json("https://x.example.com/v1/foo", b'{"x":1}', 5.0)
    assert json.loads(out) == {"code": 200, "data": []}
    # Default Content-Type + the body was forwarded.
    assert fake.calls[0]["headers"]["Content-Type"] == "application/json;charset=UTF-8"
    assert fake.calls[0]["body"] == b'{"x":1}'


def test_safe_http_post_json_raises_on_4xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeConn(
        status=403, body=json.dumps({"code": 403, "message": "denied"}).encode()
    )
    monkeypatch.setattr(
        ms_client.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )
    with pytest.raises(MiaoshouApiError) as ei:
        safe_http_post_json("https://x.example.com/v1/foo", b"{}", 5.0)
    assert ei.value.code == 403
    assert "denied" in ei.value.message


def test_safe_http_post_json_sends_extra_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeConn(status=200, body=b"{}")
    monkeypatch.setattr(
        ms_client.http.client, "HTTPSConnection", lambda *a, **kw: fake
    )
    safe_http_post_json(
        "https://x.example.com/x", b"{}", 5.0, headers={"x-custom": "1"}
    )
    assert fake.calls[0]["headers"]["x-custom"] == "1"
    # Content-Type still defaults to JSON charset.
    assert fake.calls[0]["headers"]["Content-Type"] == "application/json;charset=UTF-8"


# ─── EnvConfig ──────────────────────────────────────────────────────


def test_env_config_from_name_prod() -> None:
    cfg = EnvConfig.from_name("prod")
    assert cfg.base_url == PROD_BASE
    assert cfg.path_prefix == PROD_USER_OPEN_PREFIX
    assert cfg.name == "prod"


def test_env_config_from_name_test_default() -> None:
    cfg = EnvConfig.from_name(None)
    assert cfg.base_url == TEST_BASE
    assert cfg.path_prefix == TEST_USER_OPEN_PREFIX
    assert cfg.name == "test"


def test_env_config_from_name_lowercases() -> None:
    cfg = EnvConfig.from_name("PROD")
    assert cfg.name == "prod"


def test_env_config_from_name_invalid() -> None:
    with pytest.raises(ValueError) as ei:
        EnvConfig.from_name("staging")
    assert "MIAOSHOU_ENV" in str(ei.value)


# ─── MiaoshouClient (MD5) ───────────────────────────────────────────


@pytest.fixture()
def ms_open_client() -> MiaoshouClient:
    return MiaoshouClient(license_id=LIC, company_secret=SEC, env="test")


def test_miaoshou_client_init_lowercases_env() -> None:
    c = MiaoshouClient(license_id=LIC, company_secret=SEC, env="PROD")
    assert c.env == "prod"
    assert c.cfg.name == "prod"


def test_miaoshou_client_init_invalid_env_raises() -> None:
    """``env`` is validated through ``EnvConfig.from_name`` at construction."""
    with pytest.raises(ValueError):
        MiaoshouClient(license_id=LIC, company_secret=SEC, env="staging")


def test_miaoshou_client_context_manager() -> None:
    """Client is a no-op context manager."""
    c = MiaoshouClient(license_id=LIC, company_secret=SEC, env="test")
    with c as inner:
        assert inner is c


def test_miaoshou_client_from_env_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIAOSHOU_LICENSE_ID", LIC)
    monkeypatch.setenv("MIAOSHOU_COMPANY_SECRET", SEC)
    monkeypatch.setenv("MIAOSHOU_ENV", "test")
    monkeypatch.setenv("MIAOSHOU_HTTP_TIMEOUT", "12.5")
    c = MiaoshouClient.from_env()
    assert c.license_id == LIC
    assert c.company_secret == SEC
    assert c.timeout == 12.5


def test_miaoshou_client_from_env_missing_creds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MIAOSHOU_LICENSE_ID", raising=False)
    monkeypatch.delenv("MIAOSHOU_COMPANY_SECRET", raising=False)
    with pytest.raises(RuntimeError) as ei:
        MiaoshouClient.from_env()
    assert "MIAOSHOU_LICENSE_ID" in str(ei.value)


def test_miaoshou_client_from_env_invalid_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIAOSHOU_LICENSE_ID", LIC)
    monkeypatch.setenv("MIAOSHOU_COMPANY_SECRET", SEC)
    monkeypatch.setenv("MIAOSHOU_HTTP_TIMEOUT", "not-a-float")
    with pytest.raises(RuntimeError) as ei:
        MiaoshouClient.from_env()
    assert "MIAOSHOU_HTTP_TIMEOUT" in str(ei.value)


def test_miaoshou_client_call_returns_envelope(
    monkeypatch: pytest.MonkeyPatch, ms_open_client: MiaoshouClient
) -> None:
    """Happy-path _call: builds envelope (MD5), POSTs to base+prefix+path,
    parses code/message/data, returns MiaoshouApiResponse."""
    captured: dict[str, Any] = {}

    def fake_post(url, body_bytes, timeout, headers=None):
        captured["url"] = url
        captured["body"] = body_bytes
        captured["headers"] = headers or {}
        return json.dumps(
            {"code": 200, "message": "ok", "data": {"shops": []}}
        )

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)

    resp = ms_open_client._call(
        path="/shops/list", business_params={"page": 1, "size": 20}
    )

    assert resp.ok is True
    assert resp.code == 200
    assert resp.data == {"shops": []}
    # URL is base + path_prefix + path.
    assert captured["url"] == f"{TEST_BASE}{TEST_USER_OPEN_PREFIX}/shops/list"
    # Envelope is base64(json) + MD5 signature + licenseId + companySecret.
    payload = json.loads(captured["body"])
    assert payload["licenseId"] == LIC
    assert payload["companySecret"] == SEC
    assert isinstance(payload["sign"], str) and len(payload["sign"]) == 32
    # busData decodes back to the original business params (verify the
    # envelope round-trips through MD5 + base64).
    import base64

    bus = json.loads(base64.b64decode(payload["busData"]).decode("utf-8"))
    assert bus == {"page": 1, "size": 20}


def test_miaoshou_client_call_absolute_url(
    monkeypatch: pytest.MonkeyPatch, ms_open_client: MiaoshouClient
) -> None:
    """``absolute=True`` skips the base + path_prefix join."""
    captured: dict[str, Any] = {}

    def fake_post(url, body_bytes, timeout, headers=None):
        captured["url"] = url
        return json.dumps({"code": 200, "message": "ok", "data": {}})

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)

    ms_open_client._call(
        path="https://other.example.com/custom",
        business_params={},
        absolute=True,
    )
    assert captured["url"] == "https://other.example.com/custom"


def test_miaoshou_client_call_non_200_raises(
    monkeypatch: pytest.MonkeyPatch, ms_open_client: MiaoshouClient
) -> None:
    """Non-200 upstream code → MiaoshouApiError carrying the upstream code."""

    def fake_post(url, body_bytes, timeout, headers=None):
        return json.dumps({"code": 400, "message": "bad params", "data": None})

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)

    with pytest.raises(MiaoshouApiError) as ei:
        ms_open_client._call(path="/x", business_params={})
    assert ei.value.code == 400
    assert "bad params" in ei.value.message


def test_miaoshou_client_call_json_decode_error(
    monkeypatch: pytest.MonkeyPatch, ms_open_client: MiaoshouClient
) -> None:
    """Non-JSON upstream body → MiaoshouApiError with code=0."""

    def fake_post(url, body_bytes, timeout, headers=None):
        return "<html>oops</html>"

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)

    with pytest.raises(MiaoshouApiError) as ei:
        ms_open_client._call(path="/x")
    assert ei.value.code == 0
    assert "解析" in ei.value.message


def test_miaoshou_client_call_urllib_http_error(
    monkeypatch: pytest.MonkeyPatch, ms_open_client: MiaoshouClient
) -> None:
    """``urllib.error.HTTPError`` (e.g. from a transport-layer retry path)
    is converted to MiaoshouApiError carrying the HTTP code."""

    def fake_post(url, body_bytes, timeout, headers=None):
        raise urllib.error.HTTPError(
            url=url, code=503, msg="Service Unavailable", hdrs={}, fp=None  # type: ignore[arg-type]
        )

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)

    with pytest.raises(MiaoshouApiError) as ei:
        ms_open_client._call(path="/x")
    assert ei.value.code == 503
    assert "503" in ei.value.message


def test_miaoshou_client_call_int_code_fallback(
    monkeypatch: pytest.MonkeyPatch, ms_open_client: MiaoshouClient
) -> None:
    """An int ``code`` from the upstream is preserved as int on the response
    (the legacy HTTP-shaped envelope used by some Miaoshou endpoints).

    The open-platform layer treats anything other than ``code == 200`` as
    a failure (it does NOT use the legacy ``code == 0`` convention). So
    a ``code: 0`` upstream response still surfaces as an error here."""

    def fake_post(url, body_bytes, timeout, headers=None):
        return json.dumps({"code": 0, "message": "ok", "data": {"k": 1}})

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)

    with pytest.raises(MiaoshouApiError) as ei:
        ms_open_client._call(path="/x")
    # The int code is preserved on the error.
    assert ei.value.code == 0
    assert ei.value.message == "ok"


def test_miaoshou_client_call_string_code_non_200(
    monkeypatch: pytest.MonkeyPatch, ms_open_client: MiaoshouClient
) -> None:
    """A numeric-string code (``"400"``) is coerced through ``int()`` to an
    int code and surfaces on the error. A non-numeric string falls through
    the except branch and lands at code=0 — both are failures (anything
    other than ``200`` raises)."""

    def fake_post_intcode(url, body_bytes, timeout, headers=None):
        return json.dumps(
            {"code": "400", "message": "bad input", "data": None}
        )

    def fake_post_unparseable(url, body_bytes, timeout, headers=None):
        return json.dumps(
            {"code": "FAIL", "message": "biz fail", "data": None}
        )

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post_intcode)
    with pytest.raises(MiaoshouApiError) as ei:
        ms_open_client._call(path="/x")
    assert ei.value.code == 400
    assert "bad input" in ei.value.message

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post_unparseable)
    with pytest.raises(MiaoshouApiError) as ei:
        ms_open_client._call(path="/x")
    # Non-numeric string → int() raises → except branch → code=0.
    assert ei.value.code == 0


def test_miaoshou_client_call_debug_sign_env(
    monkeypatch: pytest.MonkeyPatch, ms_open_client: MiaoshouClient, capsys
) -> None:
    """``MIAOSHOU_DEBUG_SIGN=1`` writes a debug line to stderr (smoke test)."""
    monkeypatch.setenv("MIAOSHOU_DEBUG_SIGN", "1")

    def fake_post(url, body_bytes, timeout, headers=None):
        return json.dumps({"code": 200, "message": "ok", "data": {}})

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)
    ms_open_client._call(path="/x")
    captured = capsys.readouterr()
    assert "[miaoshou-debug]" in captured.err
    assert "envelope.sign=" in captured.err


# ─── MiaoshouErpClient (HMAC-SHA256) ────────────────────────────────


@pytest.fixture()
def ms_erp_client() -> MiaoshouErpClient:
    return MiaoshouErpClient(app_id=LIC, app_secret=SEC)


def test_miaoshou_erp_client_init_strips_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIAOSHOU_ERP_BASE_URL", "https://erp.example.com/")
    c = MiaoshouErpClient(**{"app_id": "a", "app_secret": "b"})  # noqa: S106
    assert c.base_url == "https://erp.example.com"


def test_miaoshou_erp_client_init_uses_default_base_url() -> None:
    """No env override and no explicit base_url → module-level default."""
    c = MiaoshouErpClient(**{"app_id": "a", "app_secret": "b"})  # noqa: S106
    assert c.base_url == ms_client.ERP_DEFAULT_BASE_URL


def test_miaoshou_erp_client_context_manager() -> None:
    c = MiaoshouErpClient(**{"app_id": "a", "app_secret": "b"})  # noqa: S106
    with c as inner:
        assert inner is c


def test_miaoshou_erp_client_from_env_happy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIAOSHOU_LICENSE_ID", " " + LIC + " ")
    monkeypatch.setenv("MIAOSHOU_COMPANY_SECRET", " " + SEC + " ")
    monkeypatch.setenv("MIAOSHOU_HTTP_TIMEOUT", "20")
    c = MiaoshouErpClient.from_env()
    assert c.app_id == LIC  # whitespace stripped
    assert c.app_secret == SEC
    assert c.timeout == 20.0


def test_miaoshou_erp_client_from_env_missing_creds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MIAOSHOU_LICENSE_ID", raising=False)
    monkeypatch.delenv("MIAOSHOU_COMPANY_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        MiaoshouErpClient.from_env()


def test_miaoshou_erp_client_from_env_invalid_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIAOSHOU_LICENSE_ID", LIC)
    monkeypatch.setenv("MIAOSHOU_COMPANY_SECRET", SEC)
    monkeypatch.setenv("MIAOSHOU_HTTP_TIMEOUT", "xyz")
    with pytest.raises(RuntimeError):
        MiaoshouErpClient.from_env()


def test_miaoshou_erp_call_happy(
    monkeypatch: pytest.MonkeyPatch, ms_erp_client: MiaoshouErpClient
) -> None:
    """``_call_erp`` happy path: signs the request and returns the payload."""
    captured: dict[str, Any] = {}

    def fake_post(url, body_bytes, timeout, headers=None):
        captured["url"] = url
        captured["body"] = body_bytes
        captured["headers"] = headers or {}
        return json.dumps({"result": "success", "code": "0", "data": {"ok": True}})

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)
    out = ms_erp_client._call_erp(path="/api/foo", body={"page": 1})

    assert out["result"] == "success"
    assert out["data"] == {"ok": True}
    # Auth headers must carry the HMAC signature.
    headers = captured["headers"]
    assert headers["x-app-key"] == LIC
    assert headers["x-timestamp"].isdigit()
    assert headers["x-sign"] and len(headers["x-sign"]) == 64
    # Body is JSON-serialised with ensure_ascii=True and no spaces.
    body_text = captured["body"].decode("utf-8")
    assert json.loads(body_text) == {"page": 1}


def test_miaoshou_erp_call_fail_result(
    monkeypatch: pytest.MonkeyPatch, ms_erp_client: MiaoshouErpClient
) -> None:
    """``result="fail"`` envelope → MiaoshouApiError carrying code/reason."""

    def fake_post(url, body_bytes, timeout, headers=None):
        return json.dumps(
            {"result": "fail", "code": "ACCT_NOT_FOUND", "reason": "no shop", "data": None}
        )

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)

    with pytest.raises(MiaoshouApiError) as ei:
        ms_erp_client._call_erp(path="/api/foo")
    assert ei.value.code == "ACCT_NOT_FOUND"
    assert "no shop" in ei.value.message


def test_miaoshou_erp_call_fail_result_default_code(
    monkeypatch: pytest.MonkeyPatch, ms_erp_client: MiaoshouErpClient
) -> None:
    """A fail result without ``code`` falls back to ``?``."""

    def fake_post(url, body_bytes, timeout, headers=None):
        return json.dumps({"result": "fail", "reason": "no code given"})

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)

    with pytest.raises(MiaoshouApiError) as ei:
        ms_erp_client._call_erp(path="/api/foo")
    assert ei.value.code == "?"
    assert "no code given" in ei.value.message


def test_miaoshou_erp_call_non_json_body(
    monkeypatch: pytest.MonkeyPatch, ms_erp_client: MiaoshouErpClient
) -> None:
    """Non-JSON success body → MiaoshouApiError code=0."""

    def fake_post(url, body_bytes, timeout, headers=None):
        return "<html>oops</html>"

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)

    with pytest.raises(MiaoshouApiError) as ei:
        ms_erp_client._call_erp(path="/api/foo")
    assert ei.value.code == 0
    assert "解析" in ei.value.message


def _raise_http_error(url: str, code: int, msg: str, body: bytes) -> None:
    """Helper: raise an :class:`urllib.error.HTTPError` with the given body.

    Factored out so the pyright strict-mode constructor signature
    complaints (the runtime is duck-typed; the type stubs insist on
    ``Message[str, str]`` / ``IO[bytes]``) live in one place.
    """
    fp = io.BytesIO(body)
    # HTTPError takes hdrs in real usage; we don't need it for the test
    # path, but the constructor requires *some* value. An empty BytesIO
    # works as a sentinel.
    err = urllib.error.HTTPError(  # type: ignore[call-arg]
        url=url,
        code=code,
        msg=msg,
        hdrs=cast("http.client.HTTPMessage", {}),
        fp=fp,
    )
    raise err


def test_miaoshou_erp_call_http_error_with_json_body(
    monkeypatch: pytest.MonkeyPatch, ms_erp_client: MiaoshouErpClient
) -> None:
    """``urllib.error.HTTPError`` carrying a JSON body → MiaoshouApiError
    carrying the biz code + reason from the body."""
    body = json.dumps(
        {"result": "fail", "code": "signMissing", "reason": "no x-sign header"}
    ).encode("utf-8")

    def fake_post(url, body_bytes, timeout, headers=None):
        _raise_http_error(url=url, code=400, msg="Bad Request", body=body)

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)

    with pytest.raises(MiaoshouApiError) as ei:
        ms_erp_client._call_erp(path="/api/foo")
    assert ei.value.code == "signMissing"
    assert "no x-sign header" in ei.value.message


def test_miaoshou_erp_call_http_error_without_json_body(
    monkeypatch: pytest.MonkeyPatch, ms_erp_client: MiaoshouErpClient
) -> None:
    """``urllib.error.HTTPError`` with non-JSON body → message uses raw preview."""

    def fake_post(url, body_bytes, timeout, headers=None):
        _raise_http_error(
            url=url,
            code=403,
            msg="Forbidden",
            body=b"<html>forbidden</html>",
        )

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)

    with pytest.raises(MiaoshouApiError) as ei:
        ms_erp_client._call_erp(path="/api/foo")
    assert ei.value.code == 403
    assert "HTTP 403" in ei.value.message


def test_miaoshou_erp_call_query_string(
    monkeypatch: pytest.MonkeyPatch, ms_erp_client: MiaoshouErpClient
) -> None:
    """Query params are url-encoded onto the URL, not the body."""
    captured: dict[str, Any] = {}

    def fake_post(url, body_bytes, timeout, headers=None):
        captured["url"] = url
        return json.dumps({"result": "success", "code": "0", "data": {}})

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)
    ms_erp_client._call_erp(
        path="/api/foo", body={}, query={"shopId": "1", "page": "2"}
    )
    assert "shopId=1" in captured["url"]
    assert "page=2" in captured["url"]


def test_miaoshou_erp_call_extra_body_is_compact_json(
    monkeypatch: pytest.MonkeyPatch, ms_erp_client: MiaoshouErpClient
) -> None:
    """Body JSON uses ``ensure_ascii=True`` and ``(",", ":")`` separators —
    the format the upstream signs against (matches the legacy contract)."""
    captured: dict[str, Any] = {}

    def fake_post(url, body_bytes, timeout, headers=None):
        captured["body"] = body_bytes
        return json.dumps({"result": "success", "code": "0", "data": {}})

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)
    ms_erp_client._call_erp(
        path="/api/foo", body={"k": "v", "list": [1, 2]}
    )
    # Body should be compact (no whitespace between separators).
    assert captured["body"] == b'{"k":"v","list":[1,2]}'


def test_miaoshou_erp_call_empty_body(
    monkeypatch: pytest.MonkeyPatch, ms_erp_client: MiaoshouErpClient
) -> None:
    """Empty body → empty JSON body bytes (so the HMAC is still computable)."""
    captured: dict[str, Any] = {}

    def fake_post(url, body_bytes, timeout, headers=None):
        captured["body"] = body_bytes
        return json.dumps({"result": "success", "code": "0", "data": []})

    monkeypatch.setattr(ms_client, "safe_http_post_json", fake_post)
    ms_erp_client._call_erp(path="/api/foo")
    assert captured["body"] == b""
