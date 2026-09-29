"""Typed wrappers for TikTok Shop Partner API product endpoints.

Thin typed wrappers over :class:`TiktokShopClient` for the Partner API
product-domain GETs documented in ``tts-partner-api-docs/``. Currently
covers:

* :func:`get_product` — ``GET /product/202309/products/{product_id}``

The remaining GETs in ``tts-partner-api-docs/`` (Categories / Brands /
Attributes / Category Rules / Submission Records / Image Translation
Tasks / Listing Prerequisites) are deferred to a separate work item.

Conventions
-----------
* HMAC signing + token header injection + shop_cipher placement are
  handled by :class:`TiktokShopClient`
  (``tts_erp_v2/proxy/tts_shop/client.py``).
* Each wrapper resolves credentials via :func:`load_credentials` keyed
  by internal ``shop_pk`` → upstream ``shop_id`` →
  ``access_token`` + ``shop_cipher``.
* The upstream envelope ``{code, message, data, request_id}`` is
  unwrapped: on ``code == 0`` we return ``data``; otherwise raise
  :class:`UpstreamBusinessError` carrying the upstream code + message
  + request_id verbatim so the caller can decide whether to surface it.
* No DB caching — these are live read-through wrappers. Callers that
  need offline durability should add a sync job that periodically
  captures the upstream payload into a local table.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Protocol, cast

from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.proxy.errors import ProxyError
from tts_erp_v2.proxy.tiktok_auth import resolve_tiktok_app_credentials_for_shop
from tts_erp_v2.proxy.token_service import CredentialsView, load_credentials
from tts_erp_v2.proxy.tts_shop.client import (
    DEFAULT_API_HOST,
    TiktokCallResult,
    TiktokShopClient,
)


class ProductClient(Protocol):
    """Small signing-client seam used by production and test adapters."""

    def get(
        self,
        *,
        path: str,
        access_token: str,
        extra_params: dict[str, str] | None = None,
    ) -> TiktokCallResult: ...


# Upstream path template (versioned per tts-partner-api-docs/Get Product.md).
GET_PRODUCT_PATH_TEMPLATE = "/product/202309/products/{product_id}"


class UpstreamBusinessError(ProxyError):
    """Upstream returned a non-zero ``code`` (business-level error).

    HTTP transport succeeded but the TikTok envelope ``code`` is not 0
    (e.g. ``12000002`` product not found, ``105005`` missing scope).
    Surfaces the upstream code + message + request_id verbatim so the
    caller can branch / surface / log without parsing strings.
    """

    def __init__(
        self,
        *,
        code: int,
        message: str,
        request_id: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.request_id = request_id
        super().__init__(
            f"upstream business error code={code}: {message!r} "
            f"(request_id={request_id!r})"
        )


class ChannelAccountNotFound(ProxyError):
    """commerce.shops has no row matching the internal id + platform=tiktok."""


class CredentialsMissing(ProxyError):
    """integration.credentials missing or shop_cipher empty for the given tiktok shop_id."""


def _resolve_shop_id(session: Session, shop_pk: int) -> str:
    """Look up the upstream shop_id (external_account_id) from the internal id.

    Enforces ``platform='tiktok'`` so a miaoshou account id cannot
    accidentally route through the TikTok proxy.
    """
    row = session.execute(
        text(
            "SELECT shop_id FROM commerce.shops "
            "WHERE id = :id AND platform = 'tiktok'"
        ),
        {"id": shop_pk},
    ).first()
    if row is None:
        raise ChannelAccountNotFound(
            f"commerce.shops id={shop_pk} "
            f"platform='tiktok' not found"
        )
    return row[0]


def _load_tiktok_credentials(session: Session, shop_id: str) -> CredentialsView:
    cred = load_credentials(session, provider="tiktok", external_account_id=shop_id)
    if cred is None:
        raise CredentialsMissing(
            f"integration.credentials missing for tiktok shop_id={shop_id!r}"
        )
    if not cred.shop_cipher:
        raise CredentialsMissing(
            f"tiktok shop_id={shop_id!r} credentials have empty shop_cipher "
            f"— cross-border routing requires shop_cipher"
        )
    return cred


def _build_default_client(session: Session, shop_id: str) -> TiktokShopClient:
    """Construct a client with the App pair that issued this shop token."""
    app_credentials = resolve_tiktok_app_credentials_for_shop(
        session,
        shop_id=shop_id,
    )
    api_host = os.environ.get("TIKTOK_API_HOST", "").strip() or DEFAULT_API_HOST
    return TiktokShopClient(
        app_key=app_credentials.app_key,
        app_secret=app_credentials.app_secret,
        api_host=api_host,
    )


def _check_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the ``{code, message, data, request_id}`` envelope.

    Returns the ``data`` dict on ``code == 0``. Raises
    :class:`UpstreamBusinessError` on non-zero ``code``. Raises
    :class:`ProxyError` when the envelope itself is malformed.
    """
    if not isinstance(payload, Mapping):
        raise ProxyError(
            f"upstream response is not a JSON object: {type(payload).__name__}"
        )
    code = payload.get("code")
    if code is None:
        raise ProxyError(
            f"upstream response missing 'code': keys={list(payload.keys())}"
        )
    if code != 0:
        raise UpstreamBusinessError(
            code=int(code),
            message=str(payload.get("message", "")),
            request_id=payload.get("request_id"),
        )
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ProxyError(
            f"upstream 'data' is not an object: {type(data).__name__}"
        )
    return dict(data)


def get_product(
    *,
    session: Session,
    shop_pk: int,
    product_id: str,
    return_under_review_version: bool = False,
    return_draft_version: bool = False,
    locale: str | None = None,
    client: ProductClient | None = None,
) -> dict[str, Any]:
    """Fetch one product's full details from TikTok Shop Partner API.

    Mirrors ``GET /product/202309/products/{product_id}`` per
    ``tts-partner-api-docs/Get Product.md``. Live read-through — no DB
    caching.

    The upstream response carries hundreds of fields (id, title,
    status, audit, brand, category_chains, certifications, skus,
    package dimensions, etc.). We do not model it with Pydantic — the
    client controls its own consumption of the shape. This function
    strips the envelope and returns the ``data`` payload verbatim.

    Args:
        session: SQLAlchemy session for credential + channel_account
            lookups (read-only; no mutations).
        shop_pk: Internal ``commerce.shops.id``.
            Must be a ``platform='tiktok'`` row. The upstream shop_id
            is resolved via ``external_account_id``.
        product_id: TikTok product ID (upstream returns string ids).
        return_under_review_version: Upstream flag (default ``False``).
            Mutually exclusive with ``return_draft_version`` per docs.
        return_draft_version: Upstream flag (default ``False``).
            Mutually exclusive with ``return_under_review_version``.
        locale: BCP-47 locale code (e.g. ``en-US``); ``None`` → upstream
            uses the shop's default locale.
        client: Optional pre-built :class:`TiktokShopClient` (test
            hook). ``None`` → resolve the token's issuing service_id and
            its encrypted App Key/App Secret pair.

    Returns:
        The ``data`` portion of the upstream response (full product dict).

    Raises:
        ValueError: mutually-exclusive flags set together.
        ChannelAccountNotFound: no tiktok channel_account row.
        CredentialsMissing: credentials or shop_cipher missing.
        UpstreamBusinessError: upstream ``code`` != 0.
        UpstreamHttpError / TransientProxyError / RateLimitedError /
        AuthenticationError: HTTP transport / auth failures.
        SigningError: ``TIKTOK_APP_KEY`` / ``TIKTOK_APP_SECRET`` missing.
    """
    if return_under_review_version and return_draft_version:
        raise ValueError(
            "return_under_review_version and return_draft_version are "
            "mutually exclusive per upstream docs"
        )

    shop_id = _resolve_shop_id(session, shop_pk)
    cred = _load_tiktok_credentials(session, shop_id)
    if client is None:
        client = _build_default_client(session, shop_id)

    path = GET_PRODUCT_PATH_TEMPLATE.format(product_id=product_id)
    extra_params: dict[str, str] = {
        "shop_cipher": cast(str, cred.shop_cipher)
    }
    if return_under_review_version:
        extra_params["return_under_review_version"] = "true"
    if return_draft_version:
        extra_params["return_draft_version"] = "true"
    if locale:
        extra_params["locale"] = locale

    result: TiktokCallResult = client.get(
        path=path,
        access_token=cred.access_token,
        extra_params=extra_params,
    )
    return _check_envelope(result.payload)


__all__ = [
    "GET_PRODUCT_PATH_TEMPLATE",
    "UpstreamBusinessError",
    "ChannelAccountNotFound",
    "CredentialsMissing",
    "get_product",
]