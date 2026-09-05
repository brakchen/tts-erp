"""Read-through proxies for TikTok Shop Partner API product endpoints.

Exposes ``GET /v2/tiktok-shop/products/{product_id}`` as a thin
pass-through to the upstream ``GET /product/202309/products/{product_id}``
endpoint documented in ``tts-partner-api-docs/Get Product.md``.

Auth
----
The auth middleware classifies everything under ``/v2/tiktok-shop/`` as
``readonly`` (registered in ``middleware/auth.py::_READONLY_PREFIXES``).
Any authenticated key can call the endpoint; the handler does not add
a ``require_role_at_least`` call.

Error mapping
-------------
Proxy-layer exceptions are mapped to HTTP statuses in
:func:`_map_proxy_error`. The convention:

* :class:`~products_api.UpstreamBusinessError` (upstream ``code != 0``)
  → 502 with the upstream code + message + request_id in the detail body.
* :class:`~products_api.ChannelAccountNotFound` /
  :class:`~products_api.CredentialsMissing` → 404 (operator config problem).
* :class:`~proxy_errors.AuthenticationError` → 502 (upstream auth rejected).
* :class:`~proxy_errors.RateLimitedError` → 429 (propagate the upstream limit).
* :class:`~proxy_errors.SigningError` → 500 (our config — env keys missing).
* :class:`~proxy_errors.UpstreamHttpError` /
  :class:`~proxy_errors.TransientProxyError` → 502 (upstream trouble).
* :class:`ValueError` (mutually-exclusive flags) → 422 (caller contract error).

Future endpoints
---------------
The 7 other Partner API product-domain GETs (Categories / Brands /
Attributes / Category Rules / Submission Records / Image Translation
Tasks / Listing Prerequisites) will land here as separate endpoints
under the same prefix. They are deferred to keep this change reviewable.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session
from tts_erp_v2.proxy.errors import (
    AuthenticationError,
    ProxyError,
    RateLimitedError,
    SigningError,
    TransientProxyError,
    UpstreamHttpError,
)
from tts_erp_v2.proxy.tts_shop import products_api

router = APIRouter(prefix="/v2/tiktok-shop", tags=["tiktok-shop"])


def _map_proxy_error(exc: ProxyError) -> HTTPException:
    """Translate a :class:`ProxyError` into an :class:`HTTPException`.

    The mapping is shared with future endpoints under this prefix —
    add new exception classes here once and they'll apply everywhere.
    """
    if isinstance(exc, products_api.UpstreamBusinessError):
        return HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "upstream returned a non-zero business code",
                "upstream_code": exc.code,
                "upstream_message": exc.message,
                "upstream_request_id": exc.request_id,
            },
        )
    if isinstance(exc, products_api.ChannelAccountNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, products_api.CredentialsMissing):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, RateLimitedError):
        return HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"upstream rate limit: {exc}",
        )
    if isinstance(exc, AuthenticationError):
        return HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"upstream auth rejected: {exc}",
        )
    if isinstance(exc, UpstreamHttpError):
        return HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"upstream http error: {exc}",
        )
    if isinstance(exc, TransientProxyError):
        return HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"transient upstream error: {exc}",
        )
    if isinstance(exc, SigningError):
        return HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"signing/config error: {exc}",
        )
    # Catch-all for future ProxyError subclasses that haven't been wired up.
    return HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        detail=f"proxy error: {exc}",
    )


@router.get(
    "/products/{product_id}",
    summary="Get one product's full details from TikTok Shop",
    description=(
        "**Single-source spec:** `tech-doc/api/tiktok-shop-get-product.md`. "
        "If this string disagrees with that file, the file wins.\n\n"
        "Live read-through to TikTok Shop Partner API's\n"
        "`GET /product/202309/products/{product_id}` — no DB caching. "
        "Returns the upstream `data` payload verbatim (hundreds of fields; "
        "the client controls its own consumption of the shape — we do not "
        "model it with Pydantic).\n\n"
        "**Auth.** `Authorization: Bearer <key>` or `X-API-Key: <key>`; "
        "role = `readonly`. The TikTok Partner App must have scope "
        "`seller.product.basic` enabled (else upstream returns 105005).\n\n"
        "**Required query.** `shop_pk` (int ≥ 1) — internal "
        "`commerce.shops.id`. Resolves upstream `shop_id` + "
        "`access_token` + `shop_cipher`. Must be `platform='tiktok'`.\n\n"
        "**Optional query.** `return_under_review_version` (bool, default "
        "false), `return_draft_version` (bool, default false) — mutually "
        "exclusive per upstream docs; passing both yields 422. "
        "`locale` (BCP-47 ≤ 16 chars) — `None` → upstream uses shop default.\n\n"
        "**Response.** 200 with the upstream `data` dict (envelope stripped). "
        "Errors: 401 (no key), 403 (role < readonly), 404 (channel_account or "
        "credentials missing), 422 (validation / mutually-exclusive flags), "
        "429 (upstream rate-limit after retries), 502 (upstream code != 0 / "
        "auth / http / transient), 500 (TIKTOK_APP_KEY missing). See spec doc "
        "for the full status matrix + example payloads."
    ),
    responses={
        200: {
            "description": "Upstream `data` payload returned verbatim. "
            "Shape: see upstream docs (`tts-partner-api-docs/Get Product.md`). "
            "No Pydantic model — the client parses its own subset.",
            "content": {
                "application/json": {
                    "example": {
                        "id": "1729592969712207008",
                        "title": "Premium Yoga Leggings - Black",
                        "status": "ACTIVATE",
                        "audit": {"status": "APPROVED"},
                        "brand": {"id": "7082427311584347905", "name": "Bridge nook"},
                    }
                }
            },
        },
        401: {"description": "Missing / invalid / disabled API key."},
        403: {"description": "API key role < readonly."},
        404: {
            "description": "`shop_pk` not found, or not "
            "`platform='tiktok'`, or `integration.credentials` row missing, "
            "or `shop_cipher` empty."
        },
        422: {
            "description": "Missing `shop_pk`, or "
            "`shop_pk < 1`, or mutually-exclusive flags set together."
        },
        429: {"description": "Upstream rate-limit, internal retry budget exhausted."},
        502: {
            "description": "Upstream `code != 0` (see `upstream_code` in "
            "detail), upstream auth rejected, upstream HTTP 4xx/5xx, or "
            "network blip after retries."
        },
        500: {
            "description": "`TIKTOK_APP_KEY` / `TIKTOK_APP_SECRET` / "
            "`TTS_ERP_FERNET_KEY` not configured."
        },
    },
)
def get_product(
    product_id: str,
    sess: Session = Depends(get_session),
    shop_pk: int = Query(
        ...,
        ge=1,
        description=(
            "Internal commerce.shops.id (must be platform=tiktok). "
            "Resolves the upstream shop_id + access_token + shop_cipher."
        ),
    ),
    return_under_review_version: bool = Query(
        default=False,
        description="Upstream flag. Mutually exclusive with return_draft_version.",
    ),
    return_draft_version: bool = Query(
        default=False,
        description="Upstream flag. Mutually exclusive with return_under_review_version.",
    ),
    locale: str | None = Query(
        default=None,
        max_length=16,
        description="BCP-47 locale code (e.g. en-US). None → upstream uses shop default.",
    ),
) -> dict[str, Any]:
    """Fetch one product's full details from TikTok Shop.

    Full contract: see `tech-doc/api/tiktok-shop-get-product.md`.
    """
    try:
        return products_api.get_product(
            session=sess,
            shop_pk=shop_pk,
            product_id=product_id,
            return_under_review_version=return_under_review_version,
            return_draft_version=return_draft_version,
            locale=locale,
        )
    except ValueError as exc:
        # Mutually-exclusive flags are a caller contract violation,
        # not an internal error — surface as 422, not 500.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ProxyError as exc:
        raise _map_proxy_error(exc) from exc


__all__ = ["router"]