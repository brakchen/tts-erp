"""Plugin metadata registration for the currently bound TikTok shop."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session, require_cookie_csrf, require_role_at_least
from tts_erp_v2.commerce.shop_metadata import upsert_shop_metadata
from tts_erp_v2.db.models.commerce import ChannelAccount

router = APIRouter(prefix="/v2/plugin/shops", tags=["plugin-shops"])


class SellerMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    sellerId: str = Field(min_length=1, max_length=64, pattern=r"^\d+$")
    shopName: str | None = Field(default=None, max_length=200)
    shopCode: str | None = Field(default=None, max_length=128)
    shopRegion: str | None = Field(default=None, max_length=16)
    regionCode: str | None = Field(default=None, max_length=16)

    @field_validator("shopRegion")
    @classmethod
    def market_code(cls, value: str | None) -> str | None:
        if not value:
            return None
        value = value.upper()
        if len(value) != 2 or not value.isascii() or not value.isalpha():
            raise ValueError("shopRegion must be a two-letter market code")
        return value


class DiscoveryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocolVersion: Literal[1]
    requestId: str = Field(min_length=1, max_length=128)
    source: Literal["ads-data-sync", "monitor"]
    pluginVersion: str = Field(min_length=1, max_length=64)
    observedAt: datetime
    seller: SellerMetadata

    @field_validator("observedAt")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observedAt must include a timezone")
        return value


async def _discovery_body(request: Request) -> DiscoveryBody:
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            raise HTTPException(413, "shop discovery body exceeds 8KiB")
    try:
        return DiscoveryBody.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(422, "invalid shop discovery report") from None


def _require_shop_scope(request: Request, shop_id: str) -> None:
    require_role_at_least(request, "readwrite")
    scopes = request.scope.get("api_key_scopes", ())
    if scopes and "*" not in scopes and f"seller:{shop_id}" not in scopes:
        raise HTTPException(403, "shop scope denied")


def _metadata(shop: ChannelAccount) -> dict:
    return {
        "id": shop.id, "platform": shop.platform, "shop_id": shop.shop_id,
        "account_name": shop.account_name, "region": shop.region,
        "shop_code": shop.shop_code, "opened_date": shop.opened_date,
    }


@router.post("/discover")
def discover_shop(
    request: Request,
    body: Annotated[DiscoveryBody, Depends(_discovery_body)],
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    _require_shop_scope(request, body.seller.sellerId)
    require_cookie_csrf(request)
    shop, created = upsert_shop_metadata(
        session, shop_id=body.seller.sellerId,
        account_name=body.seller.shopName or None,
        region=body.seller.shopRegion,
        region_code=body.seller.regionCode,
        shop_code=body.seller.shopCode or None,
        source=body.source, plugin_version=body.pluginVersion,
        observed_at=body.observedAt,
    )
    result = {"ok": True, "requestId": body.requestId, "created": created, "shop": _metadata(shop)}
    session.commit()
    return result


@router.get("/{shop_id}")
def get_plugin_shop(
    request: Request, shop_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    _require_shop_scope(request, shop_id)
    shop = session.scalar(select(ChannelAccount).where(
        ChannelAccount.platform == "tiktok", ChannelAccount.shop_id == shop_id,
    ))
    if shop is None:
        raise HTTPException(404, "shop not found")
    return _metadata(shop)
