"""Persistence for the media asset library.

Every function here is a short transaction over the request-scoped session.
The library is global (no shop partition): assets may be unattached, and a
digital human may reference the same asset from several personas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.media import (
    DigitalHuman,
    DigitalHumanAsset,
    MediaAsset,
)
from tts_erp_v2.media.domain import AssetKind, AssetStatus, DigitalHumanStatus


@dataclass(frozen=True, slots=True)
class AssetFilter:
    """Filters for the asset list endpoint."""

    kind: AssetKind | None = None
    keyword: str | None = None
    tag: str | None = None
    digital_human_id: int | None = None
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class AssetPage:
    """One page of assets plus the total matching count."""

    items: list[MediaAsset]
    total: int


def _asset_base_query() -> Select:
    return select(MediaAsset).where(MediaAsset.deleted_at.is_(None))


def create_asset(
    session: Session,
    *,
    kind: AssetKind,
    filename: str,
    content_type: str,
    size_bytes: int,
    bucket: str,
    object_key: str,
    uploaded_by_key_id: str | None,
    uploaded_by_prefix: str | None,
) -> MediaAsset:
    """Insert an ``awaiting_upload`` asset row and return it."""
    asset = MediaAsset(
        kind=kind.value,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        bucket=bucket,
        object_key=object_key,
        status=AssetStatus.AWAITING_UPLOAD.value,
        uploaded_by_key_id=uploaded_by_key_id,
        uploaded_by_prefix=uploaded_by_prefix,
        tags=[],
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


def replace_object_key(session: Session, asset_id: int, object_key: str) -> None:
    """Patch the object key once the id-based key can be composed."""
    session.execute(
        update(MediaAsset)
        .where(MediaAsset.id == asset_id)
        .values(object_key=object_key),
    )
    session.commit()


def get_asset(session: Session, asset_id: int) -> MediaAsset | None:
    """Return one live asset row, or ``None``."""
    return session.scalar(
        _asset_base_query().where(MediaAsset.id == asset_id),
    )


def mark_asset_ready(session: Session, asset_id: int) -> MediaAsset | None:
    """Flip an asset to ``ready`` and stamp the upload time."""
    session.execute(
        update(MediaAsset)
        .where(
            MediaAsset.id == asset_id,
            MediaAsset.deleted_at.is_(None),
        )
        .values(
            status=AssetStatus.READY.value,
            failure_reason=None,
            uploaded_at=datetime.now(UTC),
        ),
    )
    session.commit()
    return get_asset(session, asset_id)


def mark_asset_failed(session: Session, asset_id: int, reason: str) -> None:
    """Flip an asset to ``failed`` and keep the reason for the operator."""
    session.execute(
        update(MediaAsset)
        .where(
            MediaAsset.id == asset_id,
            MediaAsset.deleted_at.is_(None),
        )
        .values(status=AssetStatus.FAILED.value, failure_reason=reason),
    )
    session.commit()


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
    """Return the asset ids linked to one persona, newest link first."""
    rows = session.scalars(
        select(DigitalHumanAsset.asset_id)
        .where(DigitalHumanAsset.digital_human_id == digital_human_id)
        .order_by(DigitalHumanAsset.created_at.desc(), DigitalHumanAsset.id.desc())
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
        select(DigitalHuman).where(
            DigitalHuman.id == digital_human_id,
            DigitalHuman.deleted_at.is_(None),
        )
    )


def list_digital_humans(
    session: Session,
    *,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[DigitalHuman]:
    """List live personas, newest first."""
    query = select(DigitalHuman).where(DigitalHuman.deleted_at.is_(None))
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
) -> bool:
    """Attach one asset to one persona. Idempotent; ``False`` when unknown."""
    if get_digital_human(session, digital_human_id) is None:
        return False
    if get_asset(session, asset_id) is None:
        return False
    existing = session.scalar(
        select(DigitalHumanAsset.id).where(
            DigitalHumanAsset.digital_human_id == digital_human_id,
            DigitalHumanAsset.asset_id == asset_id,
        )
    )
    if existing is not None:
        return True
    session.add(
        DigitalHumanAsset(
            digital_human_id=digital_human_id,
            asset_id=asset_id,
        )
    )
    session.commit()
    return True


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
    "asset_ids_for_digital_human",
    "create_asset",
    "create_digital_human",
    "get_asset",
    "get_digital_human",
    "link_asset_to_digital_human",
    "list_assets",
    "list_digital_humans",
    "mark_asset_failed",
    "mark_asset_ready",
    "replace_object_key",
    "soft_delete_asset",
    "soft_delete_digital_human",
    "unlink_asset_from_digital_human",
    "update_asset_metadata",
    "update_digital_human",
]
