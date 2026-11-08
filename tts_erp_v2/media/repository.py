"""Persistence for the media asset library.

Writes use short transactions over the request-scoped session.
The library is global (no shop partition): assets may be unattached, and a
digital human may reference the same asset from several personas.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Select,
    and_,
    any_,
    bindparam,
    delete,
    func,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from tts_erp_v2.db.models.media import (
    DigitalHuman,
    DigitalHumanAsset,
    DigitalHumanPack,
    MediaAsset,
    MediaPackVideo,
)
from tts_erp_v2.media.domain import (
    AssetKind,
    AssetStatus,
    DigitalHumanStatus,
    build_object_key,
)

# Share Python's Unicode whitespace set explicitly with SQL. PostgreSQL's
# regex \s depends on its locale and can disagree for historical labels.
_TAG_WHITESPACE = (
    "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)
_TAG_SPACES = " " * len(_TAG_WHITESPACE)
_TAG_WHITESPACE_TRANSLATION = str.maketrans(_TAG_WHITESPACE, _TAG_SPACES)


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
    recent: bool = False
    after: tuple[datetime, int] | None = None
    pack_id: int | None = None
    exclude_pack_id: int | None = None


@dataclass(frozen=True, slots=True)
class AssetPage:
    """One page of assets plus the total matching count."""

    items: list[MediaAsset]
    total: int


class LinkAssetError(Enum):
    HUMAN_NOT_FOUND = "human_not_found"
    ASSET_NOT_FOUND = "asset_not_found"
    ASSET_NOT_READY = "asset_not_ready"


class DeleteAssetError(Enum):
    ASSET_IN_USE = "asset_in_use"


class PortraitNotReady(ValueError):
    """The requested portrait is unavailable or has not been confirmed."""


def normalize_tags(tags: Iterable[str]) -> list[str]:
    """Keep distinct, nonempty labels with consistent whitespace, in input order."""
    return list(dict.fromkeys(tag for value in tags if (tag := _normalize_tag(value))))


def _normalize_tag(value: str) -> str:
    return " ".join(
        part for part in value.translate(_TAG_WHITESPACE_TRANSLATION).split(" ") if part
    )


def _normalized_asset_tag() -> ColumnElement[str]:
    """Expand each stored label, including metadata saved before normalization."""
    tag = func.unnest(MediaAsset.tags).column_valued("asset_tag", joins_implicitly=True)
    translated = func.translate(tag, _TAG_WHITESPACE, _TAG_SPACES)
    return func.btrim(func.regexp_replace(translated, " +", " ", "g"))


def _asset_base_query(*, include_portrait: bool = False) -> Select:
    query = select(MediaAsset).where(MediaAsset.deleted_at.is_(None))
    return query if include_portrait else query.where(MediaAsset.is_portrait.is_(False))


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
    tags: list[str] | None = None,
    is_portrait: bool = False,
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
        tags=normalize_tags(tags or []),
        is_portrait=is_portrait,
    )
    session.add(asset)
    session.flush()
    asset.object_key = build_object_key(
        bucket=bucket, asset_id=asset.id, filename=filename, content_type=content_type
    ).key
    return asset


def get_asset(
    session: Session, asset_id: int, *, include_portrait: bool = False
) -> MediaAsset | None:
    """Return one live asset row, or ``None``."""
    return session.scalar(
        _asset_base_query(include_portrait=include_portrait).where(
            MediaAsset.id == asset_id
        ),
    )


def record_upload_verification(
    session: Session, asset_id: int, failure_reason: str | None
) -> MediaAsset | None:
    """Record a storage check without overwriting a concurrent success."""
    asset = session.scalar(
        _asset_base_query(include_portrait=True)
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
        values["tags"] = normalize_tags(tags)
    if values:
        session.execute(
            update(MediaAsset)
            .where(
                MediaAsset.id == asset_id,
                MediaAsset.deleted_at.is_(None),
                MediaAsset.is_portrait.is_(False),
            )
            .values(**values),
        )
        session.commit()
    return get_asset(session, asset_id)


def soft_delete_asset(
    session: Session, asset_id: int
) -> MediaAsset | DeleteAssetError | None:
    """Delete only an unreferenced asset, serialized with concurrent linking."""
    # link_asset_to_digital_human takes FOR SHARE on this same row. Once this
    # lock is held, no new reference can commit between the check and deletion.
    asset = session.scalar(
        _asset_base_query(include_portrait=True)
        .where(MediaAsset.id == asset_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if asset is None:
        return None
    in_use = session.scalar(
        select(
            select(DigitalHumanAsset.id)
            .join(DigitalHuman, DigitalHuman.id == DigitalHumanAsset.digital_human_id)
            .where(
                DigitalHumanAsset.asset_id == asset_id,
                DigitalHuman.deleted_at.is_(None),
            )
            .exists()
            | select(DigitalHuman.id)
            .where(
                DigitalHuman.portrait_asset_id == asset_id,
                DigitalHuman.deleted_at.is_(None),
            )
            .exists()
            | select(MediaPackVideo.asset_id)
            .where(MediaPackVideo.asset_id == asset_id)
            .exists()
        )
    )
    if in_use:
        return DeleteAssetError.ASSET_IN_USE
    session.execute(
        update(MediaAsset)
        .where(MediaAsset.id == asset_id)
        .values(deleted_at=datetime.now(UTC)),
    )
    session.commit()
    return asset


def list_assets(session: Session, filters: AssetFilter) -> AssetPage:
    """List live assets with filters and a total count."""
    conditions = [MediaAsset.deleted_at.is_(None), MediaAsset.is_portrait.is_(False)]
    if filters.kind is not None:
        conditions.append(MediaAsset.kind == filters.kind.value)
    if filters.status is not None:
        conditions.append(MediaAsset.status == filters.status.value)
    if filters.keyword:
        keyword = filters.keyword
        matching_tag = (
            select(1)
            .where(_normalized_asset_tag().icontains(keyword, autoescape=True))
            .correlate(MediaAsset)
            .exists()
        )
        conditions.append(
            or_(
                MediaAsset.filename.icontains(keyword, autoescape=True),
                MediaAsset.description.icontains(keyword, autoescape=True),
                matching_tag,
            )
        )
    if filters.tag:
        conditions.append(
            select(1)
            .where(_normalized_asset_tag() == _normalize_tag(filters.tag))
            .correlate(MediaAsset)
            .exists()
        )
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
    if filters.pack_id is not None:
        members = select(MediaPackVideo.asset_id).where(
            MediaPackVideo.pack_id == filters.pack_id
        )
        conditions.append(MediaAsset.id.in_(members))
    if filters.exclude_pack_id is not None:
        members = select(MediaPackVideo.asset_id).where(
            MediaPackVideo.pack_id == filters.exclude_pack_id
        )
        conditions.append(MediaAsset.id.not_in(members))

    total = session.scalar(
        select(func.count()).select_from(MediaAsset).where(and_(*conditions))
    )
    query = select(MediaAsset).where(and_(*conditions))
    if filters.after is not None:
        query = query.where(
            tuple_(MediaAsset.updated_at, MediaAsset.id) < tuple_(*filters.after)
        )
    order_time = MediaAsset.updated_at if filters.recent else MediaAsset.created_at
    rows = session.scalars(
        query.order_by(order_time.desc(), MediaAsset.id.desc())
        .limit(filters.limit)
        .offset(filters.offset)
    ).all()
    return AssetPage(items=list(rows), total=total or 0)


def list_asset_tags(session: Session) -> list[str]:
    """Read candidate labels from live ready assets without creating records."""
    tags = session.scalars(
        select(_normalized_asset_tag())
        .select_from(MediaAsset)
        .where(
            MediaAsset.deleted_at.is_(None),
            MediaAsset.is_portrait.is_(False),
            MediaAsset.status == AssetStatus.READY.value,
        )
        .distinct()
    ).all()
    return sorted(normalize_tags(tags))


def _asset_references(asset_ids: list[int]):
    direct = select(
        DigitalHumanAsset.asset_id, DigitalHumanAsset.digital_human_id
    ).where(DigitalHumanAsset.asset_id.in_(asset_ids))
    packed = (
        select(MediaPackVideo.asset_id, DigitalHumanPack.digital_human_id)
        .join(DigitalHumanPack, DigitalHumanPack.pack_id == MediaPackVideo.pack_id)
        .where(MediaPackVideo.asset_id.in_(asset_ids))
    )
    return direct.union(packed).subquery()


def usage_counts_for_assets(session: Session, asset_ids: list[int]) -> dict[int, int]:
    """Count live persona references, including archived personas, in one query."""
    if not asset_ids:
        return {}
    counts = dict.fromkeys(asset_ids, 0)
    references = _asset_references(asset_ids)
    rows = session.execute(
        select(references.c.asset_id, func.count())
        .join(DigitalHuman, DigitalHuman.id == references.c.digital_human_id)
        .where(DigitalHuman.deleted_at.is_(None))
        .group_by(references.c.asset_id)
    ).all()
    counts.update({asset_id: count for asset_id, count in rows})
    return counts


def asset_ids_for_digital_human(
    session: Session, digital_human_id: int, *, include_pack_videos: bool = False
) -> list[int]:
    """Return live asset ids in addition order; new references append at the end."""
    rows = session.scalars(
        select(DigitalHumanAsset.asset_id)
        .join(MediaAsset, MediaAsset.id == DigitalHumanAsset.asset_id)
        .where(
            DigitalHumanAsset.digital_human_id == digital_human_id,
            MediaAsset.deleted_at.is_(None),
        )
        .order_by(DigitalHumanAsset.created_at, DigitalHumanAsset.id)
    ).all()
    if not include_pack_videos:
        return list(rows)
    packed = session.scalars(
        select(MediaPackVideo.asset_id)
        .join(DigitalHumanPack, DigitalHumanPack.pack_id == MediaPackVideo.pack_id)
        .join(MediaAsset, MediaAsset.id == MediaPackVideo.asset_id)
        .where(
            DigitalHumanPack.digital_human_id == digital_human_id,
            MediaAsset.deleted_at.is_(None),
            MediaAsset.status == AssetStatus.READY.value,
        )
        .order_by(MediaPackVideo.created_at, MediaPackVideo.asset_id)
    )
    return list(dict.fromkeys([*rows, *packed]))


def digital_humans_for_asset(session: Session, asset_id: int) -> list[DigitalHuman]:
    """Return the live personas that reference an asset, including archived ones."""
    references = _asset_references([asset_id])
    rows = session.scalars(
        _human_base_query()
        .join(references, references.c.digital_human_id == DigitalHuman.id)
        .order_by(DigitalHuman.created_at.desc(), DigitalHuman.id.desc())
    ).all()
    return list(rows)


# --- digital humans ---------------------------------------------------------


def create_digital_human(
    session: Session,
    *,
    name: str,
    description: str | None,
    created_by: str | None,
    portrait_asset_id: int | None = None,
) -> DigitalHuman:
    """Insert one persona record."""
    if portrait_asset_id is not None:
        _require_ready_portrait(session, portrait_asset_id)
    human = DigitalHuman(
        name=name,
        description=description,
        status=DigitalHumanStatus.ACTIVE.value,
        created_by=created_by,
        portrait_asset_id=portrait_asset_id,
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


def _filtered_humans(keyword: str | None, status: DigitalHumanStatus | None) -> Select:
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
    return query


def list_digital_humans(
    session: Session,
    *,
    keyword: str | None = None,
    status: DigitalHumanStatus | None = None,
    limit: int = 50,
    offset: int = 0,
    recent: bool = False,
    after: tuple[datetime, int] | None = None,
) -> list[DigitalHuman]:
    """List live personas in creation or edit order."""
    query = _filtered_humans(keyword, status)
    if after is not None:
        query = query.where(
            tuple_(DigitalHuman.updated_at, DigitalHuman.id) < tuple_(*after)
        )
    order_time = DigitalHuman.updated_at if recent else DigitalHuman.created_at
    return list(
        session.scalars(
            query.order_by(order_time.desc(), DigitalHuman.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
    )


def count_digital_humans(
    session: Session, keyword: str | None, status: DigitalHumanStatus | None
) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(
                _filtered_humans(keyword, status).subquery()
            )
        )
        or 0
    )


def _require_ready_portrait(session: Session, asset_id: int) -> None:
    asset = session.scalar(
        _asset_base_query(include_portrait=True)
        .where(
            MediaAsset.id == asset_id,
            MediaAsset.is_portrait.is_(True),
            MediaAsset.kind == AssetKind.IMAGE.value,
            MediaAsset.status == AssetStatus.READY.value,
        )
        .with_for_update(read=True)
    )
    if asset is None:
        raise PortraitNotReady


def update_digital_human(
    session: Session,
    digital_human_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    status: DigitalHumanStatus | None = None,
    portrait_asset_id: int | None = None,
) -> DigitalHuman | None:
    """Edit one persona; ``None`` fields are left unchanged."""
    values: dict[str, object] = {}
    if name is not None:
        values["name"] = name
    if description is not None:
        values["description"] = description
    if status is not None:
        values["status"] = status.value
    if portrait_asset_id is not None:
        _require_ready_portrait(session, portrait_asset_id)
        values["portrait_asset_id"] = portrait_asset_id
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
    session.execute(
        delete(DigitalHumanPack).where(
            DigitalHumanPack.digital_human_id == digital_human_id
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
    # Lock the persona before changing links and its edited timestamp.
    human = session.scalar(
        _human_base_query().where(DigitalHuman.id == digital_human_id).with_for_update()
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
    inserted = session.execute(
        insert(DigitalHumanAsset)
        .values(digital_human_id=digital_human_id, asset_id=asset_id)
        .on_conflict_do_nothing(
            index_elements=[
                DigitalHumanAsset.digital_human_id,
                DigitalHumanAsset.asset_id,
            ]
        )
        .returning(DigitalHumanAsset.id)
    )
    if inserted.scalar_one_or_none() is not None:
        human.updated_at = datetime.now(UTC)
    session.commit()
    return human


def unlink_asset_from_digital_human(
    session: Session,
    digital_human_id: int,
    asset_id: int,
) -> bool:
    """Detach one asset from one persona. Idempotent."""
    return bool(unlink_assets_from_digital_human(session, digital_human_id, [asset_id]))


def unlink_assets_from_digital_human(
    session: Session, digital_human_id: int, asset_ids: list[int]
) -> int | None:
    """Remove the specified associations together and keep the global assets."""
    human = session.scalar(
        _human_base_query().where(DigitalHuman.id == digital_human_id).with_for_update()
    )
    if human is None:
        return None
    removed = session.scalars(
        delete(DigitalHumanAsset)
        .where(
            DigitalHumanAsset.digital_human_id == digital_human_id,
            DigitalHumanAsset.asset_id
            == any_(bindparam("unlink_ids", asset_ids, type_=ARRAY(BigInteger))),
        )
        .returning(DigitalHumanAsset.id)
    ).all()
    if removed:
        human.updated_at = datetime.now(UTC)
    session.commit()
    return len(removed)


__all__ = [
    "AssetFilter",
    "AssetPage",
    "DeleteAssetError",
    "LinkAssetError",
    "asset_ids_for_digital_human",
    "create_asset",
    "create_digital_human",
    "digital_humans_for_asset",
    "get_asset",
    "get_digital_human",
    "link_asset_to_digital_human",
    "list_asset_tags",
    "list_assets",
    "list_digital_humans",
    "normalize_tags",
    "record_upload_verification",
    "soft_delete_asset",
    "soft_delete_digital_human",
    "unlink_asset_from_digital_human",
    "update_asset_metadata",
    "update_digital_human",
    "usage_counts_for_assets",
]
