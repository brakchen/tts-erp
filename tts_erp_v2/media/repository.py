"""Persistence for the media asset library.

Writes use short transactions over the request-scoped session.
The library is global (no shop partition): assets may be unattached, and a
digital human may reference the same asset from several personas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import Select, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.media import (
    DigitalHuman,
    DigitalHumanAsset,
    MediaAsset,
)
from tts_erp_v2.media.domain import (
    AssetKind,
    AssetStatus,
    DigitalHumanStatus,
    build_object_key,
)


@dataclass(frozen=True, slots=True)
class AssetFilter:
    """Filters for the asset list endpoint."""

    kind: AssetKind | None = None
    keyword: str | None = None
    tag: str | None = None
    digital_human_id: int | None = None
    limit: int = 50
    offset: int = 0
    exclude_digital_human_id: int | None = None
    status: AssetStatus | None = None


@dataclass(frozen=True, slots=True)
class AssetPage:
    """One page of assets plus the total matching count."""

    items: list[MediaAsset]
    total: int


class LinkAssetError(Enum):
    HUMAN_NOT_FOUND = "human_not_found"
    ASSET_NOT_FOUND = "asset_not_found"
    ASSET_NOT_READY = "asset_not_ready"


def _asset_base_query() -> Select:
    return select(MediaAsset).where(MediaAsset.deleted_at.is_(None))


def _human_base_query() -> Select:
    return select(DigitalHuman).where(DigitalHuman.deleted_at.is_(None))


def create_asset(
    session: Session,
    *,
    kind: AssetKind,
    filename: str,
    content_type: str,
    size_bytes: int,
    bucket: str,
    uploaded_by_key_id: str | None,
    uploaded_by_prefix: str | None,
) -> MediaAsset:
    """Stage a ticket row with its final object key. The caller commits it after signing."""
    asset = MediaAsset(
        kind=kind.value,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        bucket=bucket,
        object_key="placeholder",
        status=AssetStatus.AWAITING_UPLOAD.value,
        uploaded_by_key_id=uploaded_by_key_id,
        uploaded_by_prefix=uploaded_by_prefix,
        tags=[],
    )
    session.add(asset)
    session.flush()
    asset.object_key = build_object_key(
        bucket=bucket, asset_id=asset.id, filename=filename, content_type=content_type
    ).key
    return asset


def get_asset(session: Session, asset_id: int) -> MediaAsset | None:
    """Return one live asset row, or ``None``."""
    return session.scalar(
        _asset_base_query().where(MediaAsset.id == asset_id),
    )


def record_upload_verification(
    session: Session, asset_id: int, failure_reason: str | None
) -> MediaAsset | None:
    """Record a storage check without overwriting a concurrent success."""
    asset = session.scalar(
        _asset_base_query()
        .where(MediaAsset.id == asset_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if asset is None:
        return None
    if asset.status != AssetStatus.READY.value:
        asset.failure_reason = failure_reason
        if failure_reason is None:
            asset.status = AssetStatus.READY.value
            asset.uploaded_at = datetime.now(UTC)
        else:
            asset.status = AssetStatus.FAILED.value
    session.commit()
    return asset


def update_asset_metadata(
    session: Session,
    asset_id: int,
    *,
    filename: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
) -> MediaAsset | None:
    """Edit the human-readable metadata of one asset.

    Only the fields the caller supplied are changed; ``None`` means "leave
    as-is" for every field except ``tags``, which is replaced when given.
    """
    values: dict[str, object] = {}
    if filename is not None:
        values["filename"] = filename
    if description is not None:
        values["description"] = description
    if tags is not None:
        values["tags"] = tags
    if values:
        session.execute(
            update(MediaAsset)
            .where(
                MediaAsset.id == asset_id,
                MediaAsset.deleted_at.is_(None),
            )
            .values(**values),
        )
        session.commit()
    return get_asset(session, asset_id)


def soft_delete_asset(session: Session, asset_id: int) -> MediaAsset | None:
    """Soft-delete one asset and return the row that carried the object key."""
    asset = get_asset(session, asset_id)
    if asset is None:
        return None
    session.execute(
        update(MediaAsset)
        .where(MediaAsset.id == asset_id)
        .values(deleted_at=datetime.now(UTC)),
    )
    session.commit()
    return asset


def list_assets(session: Session, filters: AssetFilter) -> AssetPage:
    """List live assets with filters and a total count."""
    conditions = [MediaAsset.deleted_at.is_(None)]
    if filters.kind is not None:
        conditions.append(MediaAsset.kind == filters.kind.value)
    if filters.status is not None:
        conditions.append(MediaAsset.status == filters.status.value)
    if filters.keyword:
        pattern = f"%{filters.keyword}%"
        conditions.append(
            or_(
                MediaAsset.filename.ilike(pattern),
                MediaAsset.description.ilike(pattern),
            )
        )
    if filters.tag:
        conditions.append(MediaAsset.tags.any(filters.tag))
    if filters.digital_human_id is not None:
        linked = select(DigitalHumanAsset.asset_id).where(
            DigitalHumanAsset.digital_human_id == filters.digital_human_id
        )
        conditions.append(MediaAsset.id.in_(linked))
    if filters.exclude_digital_human_id is not None:
        linked = select(DigitalHumanAsset.asset_id).where(
            DigitalHumanAsset.digital_human_id == filters.exclude_digital_human_id
        )
        conditions.append(MediaAsset.id.not_in(linked))

    total = session.scalar(
        select(func.count()).select_from(MediaAsset).where(and_(*conditions))
    )
    rows = session.scalars(
        _asset_base_query()
        .where(and_(*conditions))
        .order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
        .limit(filters.limit)
        .offset(filters.offset),
    ).all()
    return AssetPage(items=list(rows), total=total or 0)


def asset_ids_for_digital_human(
    session: Session, digital_human_id: int
) -> list[int]:
    """Return live asset ids linked to one persona, newest link first."""
    rows = session.scalars(
        select(DigitalHumanAsset.asset_id)
        .join(MediaAsset, MediaAsset.id == DigitalHumanAsset.asset_id)
        .where(
            DigitalHumanAsset.digital_human_id == digital_human_id,
            MediaAsset.deleted_at.is_(None),
        )
        .order_by(DigitalHumanAsset.created_at.desc(), DigitalHumanAsset.id.desc())
    ).all()
    return list(rows)


def digital_humans_for_asset(session: Session, asset_id: int) -> list[DigitalHuman]:
    """Return the live personas that reference an asset, including archived ones."""
    rows = session.scalars(
        _human_base_query()
        .join(DigitalHumanAsset, DigitalHumanAsset.digital_human_id == DigitalHuman.id)
        .where(DigitalHumanAsset.asset_id == asset_id)
        .order_by(DigitalHuman.name, DigitalHuman.id)
    ).all()
    return list(rows)


# --- digital humans ---------------------------------------------------------


def create_digital_human(
    session: Session,
    *,
    name: str,
    description: str | None,
    created_by: str | None,
) -> DigitalHuman:
    """Insert one persona record."""
    human = DigitalHuman(
        name=name,
        description=description,
        status=DigitalHumanStatus.ACTIVE.value,
        created_by=created_by,
    )
    session.add(human)
    session.commit()
    session.refresh(human)
    return human


def get_digital_human(session: Session, digital_human_id: int) -> DigitalHuman | None:
    """Return one live persona record, or ``None``."""
    return session.scalar(
        _human_base_query().where(DigitalHuman.id == digital_human_id)
    )


def list_digital_humans(
    session: Session,
    *,
    keyword: str | None = None,
    status: DigitalHumanStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[DigitalHuman]:
    """List live personas, newest first."""
    query = _human_base_query()
    if status is not None:
        query = query.where(DigitalHuman.status == status.value)
    if keyword:
        pattern = f"%{keyword}%"
        query = query.where(
            or_(
                DigitalHuman.name.ilike(pattern),
                DigitalHuman.description.ilike(pattern),
            )
        )
    return list(
        session.scalars(
            query.order_by(DigitalHuman.created_at.desc(), DigitalHuman.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )


def update_digital_human(
    session: Session,
    digital_human_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    status: DigitalHumanStatus | None = None,
) -> DigitalHuman | None:
    """Edit one persona; ``None`` fields are left unchanged."""
    values: dict[str, object] = {}
    if name is not None:
        values["name"] = name
    if description is not None:
        values["description"] = description
    if status is not None:
        values["status"] = status.value
    if values:
        session.execute(
            update(DigitalHuman)
            .where(
                DigitalHuman.id == digital_human_id,
                DigitalHuman.deleted_at.is_(None),
            )
            .values(**values),
        )
        session.commit()
    return get_digital_human(session, digital_human_id)


def soft_delete_digital_human(session: Session, digital_human_id: int) -> bool:
    """Soft-delete one persona and drop its asset links."""
    human = get_digital_human(session, digital_human_id)
    if human is None:
        return False
    session.execute(
        update(DigitalHuman)
        .where(DigitalHuman.id == digital_human_id)
        .values(deleted_at=datetime.now(UTC)),
    )
    session.execute(
        delete(DigitalHumanAsset).where(
            DigitalHumanAsset.digital_human_id == digital_human_id
        )
    )
    session.commit()
    return True


def link_asset_to_digital_human(
    session: Session,
    digital_human_id: int,
    asset_id: int,
) -> DigitalHuman | LinkAssetError:
    """Attach a ready asset or acknowledge a legacy link, including on retries."""
    # Shared row locks serialize this decision with soft deletes. The unique
    # pair constraint still allows concurrent attempts to converge on one link.
    human = session.scalar(
        _human_base_query()
        .where(DigitalHuman.id == digital_human_id)
        .with_for_update(read=True)
    )
    if human is None:
        return LinkAssetError.HUMAN_NOT_FOUND
    asset = session.scalar(
        _asset_base_query().where(MediaAsset.id == asset_id).with_for_update(read=True)
    )
    if asset is None:
        return LinkAssetError.ASSET_NOT_FOUND
    if asset.status != AssetStatus.READY.value:
        existing = session.scalar(
            select(DigitalHumanAsset.id)
            .where(
                DigitalHumanAsset.digital_human_id == digital_human_id,
                DigitalHumanAsset.asset_id == asset_id,
            )
            .with_for_update(read=True)
        )
        return human if existing is not None else LinkAssetError.ASSET_NOT_READY
    session.execute(
        insert(DigitalHumanAsset)
        .values(digital_human_id=digital_human_id, asset_id=asset_id)
        .on_conflict_do_nothing(
            index_elements=[
                DigitalHumanAsset.digital_human_id,
                DigitalHumanAsset.asset_id,
            ]
        )
    )
    session.commit()
    return human


def unlink_asset_from_digital_human(
    session: Session,
    digital_human_id: int,
    asset_id: int,
) -> bool:
    """Detach one asset from one persona. Idempotent."""
    result = session.execute(
        delete(DigitalHumanAsset).where(
            DigitalHumanAsset.digital_human_id == digital_human_id,
            DigitalHumanAsset.asset_id == asset_id,
        )
    )
    session.commit()
    return bool(result.rowcount)


__all__ = [
    "AssetFilter",
    "AssetPage",
    "LinkAssetError",
    "asset_ids_for_digital_human",
    "create_asset",
    "create_digital_human",
    "digital_humans_for_asset",
    "get_asset",
    "get_digital_human",
    "link_asset_to_digital_human",
    "list_assets",
    "list_digital_humans",
    "record_upload_verification",
    "soft_delete_asset",
    "soft_delete_digital_human",
    "unlink_asset_from_digital_human",
    "update_asset_metadata",
    "update_digital_human",
]
