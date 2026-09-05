"""TDD tests for ``GET /v2/tiktok-shop/products/{product_id}``.

Read-through proxy to TikTok Shop's
``GET /product/202309/products/{product_id}`` per
``tts-partner-api-docs/Get Product.md``. No DB caching — the handler
delegates to ``tts_erp_v2.proxy.tts_shop.products_api.get_product`` and
returns the upstream ``data`` payload verbatim.

Coverage
--------
* 200 happy path: upstream data returned verbatim
* 401 missing key (auth middleware)
* 422 missing shop_pk query param
* 422 shop_pk < 1
* 422 mutually exclusive flags (handler-side ValueError → 422)
* 404 ChannelAccountNotFound
* 404 CredentialsMissing
* 502 UpstreamBusinessError (with upstream_code/message/request_id in detail)
* 502 AuthenticationError
* 429 RateLimitedError
* 502 UpstreamHttpError
* 502 TransientProxyError
* 500 SigningError
* Query param coercion: return_under_review_version=false omitted,
  locale passes through.

Mocking pattern: ``monkeypatch.setattr`` replaces
``tts_erp_v2.proxy.tts_shop.products_api.get_product`` with a
controllable fake. Auth uses the ``readonly_key`` fixture from
``tests/api/conftest.py`` (committed outside the per-test savepoint).
"""

from __future__ import annotations

from typing import Any

import pytest

from tts_erp_v2.proxy.errors import (
    AuthenticationError,
    RateLimitedError,
    SigningError,
    TransientProxyError,
    UpstreamHttpError,
)
from tts_erp_v2.proxy.tts_shop import products_api

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

ENDPOINT = "/v2/tiktok-shop/products/{product_id}"
PRODUCT_ID = "1729592969712207008"


# ---------------------------------------------------------------------------
# Stub for the proxy function — every test sets .return_value or .side_effect.
# ---------------------------------------------------------------------------


class _ProxyStub:
    """Replaces ``products_api.get_product`` for one test."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.return_value: dict[str, Any] = {"id": PRODUCT_ID, "title": "stub"}
        self.side_effect: BaseException | None = None

    def __call__(
        self,
        *,
        session: Any,
        shop_pk: int,
        product_id: str,
        return_under_review_version: bool = False,
        return_draft_version: bool = False,
        locale: str | None = None,
        client: Any = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "shop_pk": shop_pk,
                "product_id": product_id,
                "return_under_review_version": return_under_review_version,
                "return_draft_version": return_draft_version,
                "locale": locale,
            }
        )
        if self.side_effect is not None:
            raise self.side_effect
        return self.return_value


@pytest.fixture()
def proxy_stub(monkeypatch):
    """Replace ``products_api.get_product`` with a controllable stub."""
    stub = _ProxyStub()
    monkeypatch.setattr(products_api, "get_product", stub)
    return stub


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_get_product_returns_upstream_data(api_client, readonly_key, proxy_stub):
    proxy_stub.return_value = {"id": PRODUCT_ID, "title": "Test Tee", "status": "ACTIVATE"}

    r = api_client.get(
        ENDPOINT.format(product_id=PRODUCT_ID),
        params={"shop_pk": 1},
        headers=_auth_headers(readonly_key),
    )

    assert r.status_code == 200, r.text
    assert r.json() == {"id": PRODUCT_ID, "title": "Test Tee", "status": "ACTIVATE"}


def test_get_product_passes_query_params_to_proxy(api_client, readonly_key, proxy_stub):
    proxy_stub.return_value = {"id": PRODUCT_ID}

    r = api_client.get(
        ENDPOINT.format(product_id=PRODUCT_ID),
        params={
            "shop_pk": 42,
            "return_under_review_version": "true",
            "locale": "en-US",
        },
        headers=_auth_headers(readonly_key),
    )

    assert r.status_code == 200, r.text
    assert len(proxy_stub.calls) == 1
    call = proxy_stub.calls[0]
    assert call["shop_pk"] == 42
    assert call["product_id"] == PRODUCT_ID
    assert call["return_under_review_version"] is True
    assert call["return_draft_version"] is False
    assert call["locale"] == "en-US"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_get_product_401_without_key(api_client, proxy_stub):
    r = api_client.get(
        ENDPOINT.format(product_id=PRODUCT_ID),
        params={"shop_pk": 1},
    )

    assert r.status_code == 401, r.text
    # Auth middleware rejects before the handler — proxy untouched.
    assert proxy_stub.calls == []


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


def test_get_product_422_without_shop_pk(api_client, readonly_key, proxy_stub):
    r = api_client.get(
        ENDPOINT.format(product_id=PRODUCT_ID),
        headers=_auth_headers(readonly_key),
    )

    assert r.status_code == 422, r.text
    assert proxy_stub.calls == []


def test_get_product_422_shop_pk_below_one(api_client, readonly_key, proxy_stub):
    r = api_client.get(
        ENDPOINT.format(product_id=PRODUCT_ID),
        params={"shop_pk": 0},
        headers=_auth_headers(readonly_key),
    )

    assert r.status_code == 422, r.text
    assert proxy_stub.calls == []


def test_get_product_422_mutually_exclusive_flags(api_client, readonly_key, proxy_stub):
    """Mutually exclusive flags trigger a ValueError inside the proxy; the
    handler maps it to 422 (not 500) so the client sees the contract
    violation rather than an opaque internal error."""

    proxy_stub.side_effect = ValueError(
        "return_under_review_version and return_draft_version are mutually exclusive"
    )

    r = api_client.get(
        ENDPOINT.format(product_id=PRODUCT_ID),
        params={
            "shop_pk": 1,
            "return_under_review_version": "true",
            "return_draft_version": "true",
        },
        headers=_auth_headers(readonly_key),
    )

    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Proxy error mapping
# ---------------------------------------------------------------------------


def test_get_product_404_channel_account_not_found(api_client, readonly_key, proxy_stub):
    proxy_stub.side_effect = products_api.ChannelAccountNotFound(
        "commerce.shops id=42 platform='tiktok' not found"
    )

    r = api_client.get(
        ENDPOINT.format(product_id=PRODUCT_ID),
        params={"shop_pk": 42},
        headers=_auth_headers(readonly_key),
    )

    assert r.status_code == 404, r.text
    assert "not found" in r.json()["detail"]


def test_get_product_404_credentials_missing(api_client, readonly_key, proxy_stub):
    proxy_stub.side_effect = products_api.CredentialsMissing(
        "integration.credentials missing for tiktok shop_id='TEST_shop'"
    )

    r = api_client.get(
        ENDPOINT.format(product_id=PRODUCT_ID),
        params={"shop_pk": 1},
        headers=_auth_headers(readonly_key),
    )

    assert r.status_code == 404, r.text
    assert "credentials" in r.json()["detail"].lower()


def test_get_product_502_upstream_business_error(api_client, readonly_key, proxy_stub):
    """Upstream ``code != 0`` → 502 with the upstream code/message/request_id
    surfaced in the detail body so callers can branch without re-fetching."""
    proxy_stub.side_effect = products_api.UpstreamBusinessError(
        code=12000002,
        message="product not found",
        request_id="req_err_001",
    )

    r = api_client.get(
        ENDPOINT.format(product_id=PRODUCT_ID),
        params={"shop_pk": 1},
        headers=_auth_headers(readonly_key),
    )

    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail["upstream_code"] == 12000002
    assert detail["upstream_message"] == "product not found"
    assert detail["upstream_request_id"] == "req_err_001"


def test_get_product_502_authentication_error(api_client, readonly_key, proxy_stub):
    proxy_stub.side_effect = AuthenticationError(
        "upstream auth rejected (401): token expired"
    )

    r = api_client.get(
        ENDPOINT.format(product_id=PRODUCT_ID),
        params={"shop_pk": 1},
        headers=_auth_headers(readonly_key),
    )

    assert r.status_code == 502, r.text
    assert "auth rejected" in r.json()["detail"]


def test_get_product_429_rate_limited(api_client, readonly_key, proxy_stub):
    proxy_stub.side_effect = RateLimitedError(
        "upstream 429 after retries",
        body_preview="too many requests",
    )

    r = api_client.get(
        ENDPOINT.format(product_id=PRODUCT_ID),
        params={"shop_pk": 1},
        headers=_auth_headers(readonly_key),
    )

    assert r.status_code == 429, r.text


def test_get_product_502_upstream_http_error(api_client, readonly_key, proxy_stub):
    proxy_stub.side_effect = UpstreamHttpError(
        status_code=400,
        message="invalid parameter",
        upstream_code=36009004,
    )

    r = api_client.get(
        ENDPOINT.format(product_id=PRODUCT_ID),
        params={"shop_pk": 1},
        headers=_auth_headers(readonly_key),
    )

    assert r.status_code == 502, r.text


def test_get_product_502_transient_error(api_client, readonly_key, proxy_stub):
    proxy_stub.side_effect = TransientProxyError(
        "network error after 3 attempts: ConnectionResetError"
    )

    r = api_client.get(
        ENDPOINT.format(product_id=PRODUCT_ID),
        params={"shop_pk": 1},
        headers=_auth_headers(readonly_key),
    )

    assert r.status_code == 502, r.text


def test_get_product_500_signing_error(api_client, readonly_key, proxy_stub):
    """TIKTOK_APP_KEY / TIKTOK_APP_SECRET not configured → our config error."""
    proxy_stub.side_effect = SigningError(
        "TIKTOK_APP_KEY / TIKTOK_APP_SECRET not configured"
    )

    r = api_client.get(
        ENDPOINT.format(product_id=PRODUCT_ID),
        params={"shop_pk": 1},
        headers=_auth_headers(readonly_key),
    )

    assert r.status_code == 500, r.text


# ---------------------------------------------------------------------------
# OpenAPI metadata — regression guard so a future refactor doesn't drop the
# contract docs from /docs Swagger UI.
# ---------------------------------------------------------------------------


PATH_KEY = "/v2/tiktok-shop/products/{product_id}"
REQUIRED_RESPONSES = {"200", "401", "403", "404", "422", "429", "502", "500"}


def _get_openapi_path(api_client, path_key: str) -> dict:
    r = api_client.get("/openapi.json")
    assert r.status_code == 200, r.text
    return r.json()["paths"][path_key]["get"]


def test_openapi_summary_is_set(api_client):
    """`summary` is the headline shown in the Swagger UI list view."""
    op = _get_openapi_path(api_client, PATH_KEY)
    summary = op.get("summary", "")
    assert summary, "summary is empty — Swagger UI list view will be blank"
    assert "TikTok" in summary
    assert "product" in summary.lower()


def test_openapi_description_references_spec_doc(api_client):
    """`description` must point at the single-source spec doc."""
    op = _get_openapi_path(api_client, PATH_KEY)
    desc = op.get("description", "")
    assert desc, "description is empty — Swagger UI detail view will be blank"
    assert "tech-doc/api/tiktok-shop-get-product.md" in desc, (
        "description must name the canonical spec doc so the two "
        "don't drift"
    )
    # The contract key points must all appear in the description so a
    # reader can find them at a glance.
    for keyword in (
        "shop_pk",
        "return_under_review_version",
        "return_draft_version",
        "locale",
        "seller.product.basic",
        "readonly",
        "502",
    ):
        assert keyword in desc, f"description missing keyword: {keyword!r}"


def test_openapi_responses_documents_full_status_matrix(api_client):
    """Every status code in the spec must be in `responses` so Swagger UI
    shows them in the Responses block."""
    op = _get_openapi_path(api_client, PATH_KEY)
    responses = op.get("responses", {})
    assert responses, "responses map is empty"
    missing = REQUIRED_RESPONSES - set(responses.keys())
    assert not missing, f"missing status codes in OpenAPI: {sorted(missing)}"


def test_openapi_query_params_have_descriptions(api_client):
    """Each query parameter must have a description so the Swagger UI
    Try-It-Out panel explains it."""
    op = _get_openapi_path(api_client, PATH_KEY)
    params = op.get("parameters", [])
    by_name = {p["name"]: p for p in params}

    for name in (
        "shop_pk",
        "return_under_review_version",
        "return_draft_version",
        "locale",
    ):
        assert name in by_name, f"missing OpenAPI parameter: {name!r}"
        desc = by_name[name].get("description", "")
        assert desc, f"OpenAPI parameter {name!r} has no description"