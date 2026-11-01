"""Create one shop and fill missing metadata without changing existing values."""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.commerce import ChannelAccount


def upsert_shop_metadata(
    session: Session,
    *,
    shop_id: str,
    account_name: str | None = None,
    region: str | None = None,
    shop_code: str | None = None,
    seller_type: str | None = None,
    opened_date: date | None = None,
    service_id: str | None = None,
    source: str | None = None,
    plugin_version: str | None = None,
    observed_at: datetime | None = None,
    region_code: str | None = None,
) -> tuple[ChannelAccount, bool]:
    now = datetime.now(UTC)
    values: dict[str, Any] = dict(
        platform="tiktok", shop_id=shop_id, account_name=account_name,
        region=region, shop_code=shop_code, seller_type=seller_type,
        opened_date=opened_date, service_id=service_id,
        discovered_at=now if source else None,
    )
    stmt = insert(ChannelAccount).values(**values)
    updates = {
        field: func.coalesce(func.nullif(getattr(ChannelAccount, field), ""), getattr(stmt.excluded, field))
        for field in ("account_name", "region", "shop_code", "seller_type", "service_id")
    }
    updates["opened_date"] = func.coalesce(ChannelAccount.opened_date, stmt.excluded.opened_date)
    updates["discovered_at"] = func.coalesce(ChannelAccount.discovered_at, stmt.excluded.discovered_at)
    updates["updated_at"] = now
    shop, created = session.execute(
        stmt.on_conflict_do_update(index_elements=["platform", "shop_id"], set_=updates)
        .returning(ChannelAccount, literal_column("xmax = 0"))
        .execution_options(populate_existing=True)
    ).one()
    if source:
        # The upsert holds the row lock until the caller commits. Replace the
        # JSON object, rather than mutating it in-place, so ORM tracks changes.
        metadata = dict(shop.plugin_metadata or {})
        plugins = dict(metadata.get("plugins", {}))
        previous = plugins.get(source, {})
        at = observed_at or now
        previous_at = previous.get("observedAt")
        if not previous_at or at >= datetime.fromisoformat(previous_at):
            plugins[source] = {
                "version": plugin_version,
                "lastReceivedAt": now.isoformat(),
                "observedAt": at.isoformat(),
                "values": {"shopName": account_name, "shopRegion": region,
                           "shopCode": shop_code, "regionCode": region_code},
            }
        else:
            plugins[source] = {**previous, "lastReceivedAt": now.isoformat()}
        shop.plugin_metadata = {**metadata, "plugins": plugins}
        session.flush()
    return shop, bool(created)


def plugin_sources(metadata: dict | None) -> list[dict]:
    return [
        {"source": source, "last_received_at": value.get("lastReceivedAt"),
         "observed_at": value.get("observedAt"), "plugin_version": value.get("version")}
        for source, value in sorted((metadata or {}).get("plugins", {}).items())
    ]
