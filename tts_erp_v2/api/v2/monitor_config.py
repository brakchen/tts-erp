"""``/v2/monitor/*`` — the shop-scoped monitor remote-configuration surface.

Phase B adds exactly one read path, ``GET /v2/monitor/config``
(docs/monitor-config-sync.md §6.1). It is deliberately thin: authorization and
identity resolution happen here, envelope composition lives in
:mod:`tts_erp_v2.monitor_config.delivery`.

Security boundary, in the order the checks must run:

1. Re-verify the credential **uncached** with
   :func:`authenticate_monitor_delivery_key`. The middleware already refused
   non-monitor keys, but it decided on a possibly cached credential; a key
   disabled or rotated since must stop working on the next request
   (§4.2 约束 5).
2. Only then look at ``If-None-Match``. A correct ETag must never be able to
   bypass authentication, shop matching, or existence checks (§4.2 约束 3).
3. Resolve ``sellerId`` to a registered TikTok shop and require an exact match
   against the credential's ``shop_pk``.
4. Load the frozen delivery; an onboarded shop with nothing published is 404.

No credential, cookie, encrypted blob, or token value is ever echoed into the
response body, headers, or a log record.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from tts_erp_v2.access import authenticate_monitor_delivery_key
from tts_erp_v2.api.deps import SessionDep
from tts_erp_v2.monitor_config.delivery import load_delivery, resolve_shop
from tts_erp_v2.monitor_config.etag import delivery_headers, is_not_modified

router = APIRouter(prefix="/v2/monitor", tags=["monitor-config"])


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header:
        scheme, _, value = header.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    return request.headers.get("x-api-key")


@router.get("/config")
def monitor_delivery_config(
    request: Request,
    sellerId: str = Query(..., min_length=1),
    sess: SessionDep = None,  # type: ignore[assignment]
) -> Response:
    """Return this shop's frozen monitor configuration snapshot."""

    # 1. Authoritative, cache-free credential check — before any conditional
    #    evaluation, so a stale ETag cannot turn into a 304 for a revoked key.
    credential = authenticate_monitor_delivery_key(_bearer_token(request) or "")
    if credential is None:
        # Do not distinguish "missing" from "rejected": the middleware already
        # answers 401 for anonymous callers on this route.
        raise HTTPException(status_code=403, detail="monitor delivery token required")

    # 2. Identity is the single sellerId field; never a fallback global config.
    shop = resolve_shop(sess, sellerId)
    if shop is None:
        raise HTTPException(status_code=404, detail="sellerId not registered")
    if credential.shop_pk != shop.id:
        raise HTTPException(status_code=403, detail="token is not bound to this shop")

    # 3. Existence check, still ahead of the conditional comparison.
    view = load_delivery(sess, shop.id, shop.shop_id)
    if view is None:
        raise HTTPException(status_code=404, detail="no published configuration")

    # 4. Only now may If-None-Match short-circuit the body.
    if is_not_modified(request.headers.get("if-none-match"), view.etag):
        return Response(status_code=304, headers=delivery_headers(view.etag))
    return JSONResponse(content=view.body, headers=delivery_headers(view.etag))
