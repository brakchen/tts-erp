"""``/v2/monitor/*`` — the shop-scoped monitor remote-configuration surface.

Phase B added the read path ``GET /v2/monitor/config``
(docs/monitor-config-sync.md §6.1); Phase C adds the management surface
``/v2/monitor/admin/*`` (§6.3). Both routers are deliberately thin: identity,
authorization and conflict mapping happen here, while envelope composition and
publish orchestration live in :mod:`tts_erp_v2.monitor_config.delivery` and
:mod:`tts_erp_v2.monitor_config.admin`.

Security boundary of the read path, in the order the checks must run:

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

The management paths are the opposite direction: ``readwrite`` or above, never
a monitor delivery token (§6.3), and they never resolve a secret — drafts and
lists carry ``secret://`` references and the only decrypted copy is the frozen
delivery the plugin already receives.

No credential, cookie, encrypted blob, or token value is ever echoed into the
response body, headers, or a log record.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from tts_erp_v2.access import authenticate_monitor_delivery_key
from tts_erp_v2.api.deps import SessionDep, require_role_at_least
from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.monitor_config import admin, contracts
from tts_erp_v2.monitor_config.delivery import (
    ForceRequiresPublishedDelivery,
    ForceRequiresRequestId,
    ForceVersionConflict,
    PublishConflict,
    force_publish,
    load_delivery,
    resolve_shop,
)
from tts_erp_v2.monitor_config.etag import delivery_headers, is_not_modified
from tts_erp_v2.monitor_config.keys import GLOBAL_DEFAULTS_KEY, shop_config_key
from tts_erp_v2.runtime_config.validation import ConfigValidationError

router = APIRouter(prefix="/v2/monitor", tags=["monitor-config"])

ADMIN_ROLE = "readwrite"


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


# ─── management surface (§6.3)────────────────────────────────────
#
# Every route below requires ``readwrite`` (enforced by the access layer's role
# matrix and re-checked here) and is refused outright for a monitor delivery
# token: that key's whole purpose is one shop's read (§4.2 约束 2).


class _MonitorWireModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class MonitorDraftUpdate(_MonitorWireModel):
    model_config = ConfigDict(extra="forbid")

    expected_draft_version: int = Field(alias="expectedDraftVersion", ge=0)
    payload: dict[str, Any]


class MonitorPublish(_MonitorWireModel):
    model_config = ConfigDict(extra="forbid")

    expected_draft_version: int = Field(alias="expectedDraftVersion", ge=0)
    comment: str | None = Field(default=None, max_length=2_000)


class MonitorForce(_MonitorWireModel):
    model_config = ConfigDict(extra="forbid")

    # Decimal string, like every other delivery identifier that crosses the
    # plugin wire (§5.2); the admin list echoes it back verbatim.
    expected_delivery_version: str = Field(
        alias="expectedDeliveryVersion", pattern=r"^[0-9]+$"
    )
    request_id: uuid.UUID = Field(alias="requestId")


def _actor(request: Request) -> str:
    """Opaque actor id, matching the runtime-config audit convention."""

    key_hash = request.scope.get("api_key_hash")
    if isinstance(key_hash, str) and key_hash:
        return f"api_key:{key_hash[:16]}"
    return "browser-session"


def _draft_out(view: admin.DraftView) -> dict[str, Any]:
    return {
        "configKey": view.config_key,
        "draftPayload": view.draft_payload,
        "draftVersion": view.draft_version,
        "publishedVersion": view.published_version,
        "publishedPayload": view.published_payload,
        "publishedAt": view.published_at.isoformat() if view.published_at else None,
        "updatedAt": view.updated_at.isoformat() if view.updated_at else None,
    }


def _deliveries_out(outcome: admin.PublishOutcome) -> list[dict[str, Any]]:
    return [
        {
            "shopPk": str(item.shop_pk),
            "sellerId": item.seller_id,
            "deliveryVersion": str(item.delivery_version),
            "isForce": item.is_force,
        }
        for item in outcome.deliveries
    ]


def _publish_out(outcome: admin.PublishOutcome) -> dict[str, Any]:
    return {
        "configKey": outcome.config_key,
        "publishedVersion": outcome.published_version,
        "deliveries": _deliveries_out(outcome),
    }


def _admin_http_error(exc: Exception) -> HTTPException:
    """Map one orchestration failure onto the §6.2 status vocabulary."""

    if isinstance(
        exc,
        (admin.DraftVersionConflict, ForceVersionConflict, PublishConflict),
    ):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (ForceRequiresPublishedDelivery, admin.MissingGlobalDefault)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(
        exc,
        (
            admin.ForeignSecretReference,
            admin.MissingSecret,
            admin.ConfigItemRetired,
            admin.UnknownShop,
            admin.NothingToPublish,
            ForceRequiresRequestId,
            contracts.ConfigContractError,
            ConfigValidationError,
            KeyError,
            TypeError,
        ),
    ):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc


def _shop_or_404(sess: Any, shop_pk: int) -> ChannelAccount:
    shop = sess.get(ChannelAccount, shop_pk)
    if shop is None or shop.platform != resolve_platform(sess):
        raise HTTPException(status_code=404, detail="shop not found")
    return shop


def resolve_platform(sess: Any) -> str:
    from tts_erp_v2.monitor_config.delivery import PLATFORM

    return PLATFORM


@router.get("/admin/shops")
def monitor_admin_shops(
    request: Request,
    sess: SessionDep = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    require_role_at_least(request, ADMIN_ROLE)
    return {"items": admin.list_managed_shops(sess)}


@router.get("/admin/defaults/draft")
def monitor_admin_defaults_draft(
    request: Request,
    sess: SessionDep = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    require_role_at_least(request, ADMIN_ROLE)
    return _draft_out(admin.read_draft(sess, GLOBAL_DEFAULTS_KEY))


@router.put("/admin/defaults/draft")
def monitor_admin_save_defaults_draft(
    body: MonitorDraftUpdate,
    request: Request,
    sess: SessionDep = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    require_role_at_least(request, ADMIN_ROLE)
    try:
        view = admin.save_draft(
            sess,
            config_key=GLOBAL_DEFAULTS_KEY,
            display_name="monitor 全局默认",
            payload=body.payload,
            expected_draft_version=body.expected_draft_version,
        )
    except (admin.DraftVersionConflict, admin.ConfigItemRetired) as exc:
        raise _admin_http_error(exc) from exc
    except Exception as exc:  # mapped to the §6.2 status vocabulary below
        raise _admin_http_error(exc) from exc
    sess.commit()
    return _draft_out(view)


@router.post("/admin/defaults/publish")
def monitor_admin_publish_defaults(
    body: MonitorPublish,
    request: Request,
    sess: SessionDep = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    require_role_at_least(request, ADMIN_ROLE)
    try:
        outcome = admin.publish_defaults(
            sess,
            expected_draft_version=body.expected_draft_version,
            comment=body.comment,
            actor=_actor(request),
        )
    except Exception as exc:  # mapped to the §6.2 status vocabulary below
        raise _admin_http_error(exc) from exc
    sess.commit()
    return _publish_out(outcome)


@router.get("/admin/shops/{shop_pk}/draft")
def monitor_admin_shop_draft(
    shop_pk: int,
    request: Request,
    sess: SessionDep = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    require_role_at_least(request, ADMIN_ROLE)
    _shop_or_404(sess, shop_pk)
    return _draft_out(admin.read_draft(sess, shop_config_key(shop_pk)))


@router.put("/admin/shops/{shop_pk}/draft")
def monitor_admin_save_shop_draft(
    shop_pk: int,
    body: MonitorDraftUpdate,
    request: Request,
    sess: SessionDep = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    require_role_at_least(request, ADMIN_ROLE)
    shop = _shop_or_404(sess, shop_pk)
    config_key = shop_config_key(shop_pk)
    try:
        view = admin.save_draft(
            sess,
            config_key=config_key,
            display_name=f"monitor 店铺覆盖 · {shop.shop_id}",
            payload=body.payload,
            expected_draft_version=body.expected_draft_version,
        )
    except Exception as exc:  # mapped to the §6.2 status vocabulary below
        raise _admin_http_error(exc) from exc
    sess.commit()
    return _draft_out(view)


@router.post("/admin/shops/{shop_pk}/publish")
def monitor_admin_publish_shop(
    shop_pk: int,
    body: MonitorPublish,
    request: Request,
    sess: SessionDep = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    require_role_at_least(request, ADMIN_ROLE)
    shop = _shop_or_404(sess, shop_pk)
    try:
        outcome = admin.publish_shop(
            sess,
            shop_pk=shop_pk,
            seller_id=shop.shop_id,
            expected_draft_version=body.expected_draft_version,
            comment=body.comment,
            actor=_actor(request),
        )
    except Exception as exc:  # mapped to the §6.2 status vocabulary below
        raise _admin_http_error(exc) from exc
    sess.commit()
    return _publish_out(outcome)


@router.post("/admin/shops/{shop_pk}/force")
def monitor_admin_force_shop(
    shop_pk: int,
    body: MonitorForce,
    request: Request,
    sess: SessionDep = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    require_role_at_least(request, ADMIN_ROLE)
    _shop_or_404(sess, shop_pk)
    expected_version = int(body.expected_delivery_version)
    try:
        row = force_publish(
            sess,
            shop_pk=shop_pk,
            expected_delivery_version=expected_version,
            request_id=body.request_id,
            request_digest=admin.force_request_digest(shop_pk, expected_version),
            created_by=_actor(request),
        )
    except Exception as exc:  # mapped to the §6.2 status vocabulary below
        raise _admin_http_error(exc) from exc
    sess.commit()
    return {
        "shopPk": str(shop_pk),
        "deliveryVersion": str(row.version),
        "isForce": bool(row.is_force),
        "contentHash": row.content_hash,
        "sourceVersions": {
            "global": (
                str(row.source_global_version)
                if row.source_global_version is not None
                else None
            ),
            "shop": (
                str(row.source_shop_version)
                if row.source_shop_version is not None
                else None
            ),
        },
        "publishedAt": row.created_at.isoformat() if row.created_at else None,
    }
