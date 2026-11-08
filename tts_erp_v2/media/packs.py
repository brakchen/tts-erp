"""Persistence for shared video packs and their associations."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.media import (
    DigitalHuman,
    DigitalHumanPack,
    MediaAsset,
    MediaPack,
    MediaPackVideo,
)


class PackError(ValueError):
    """Stable error code for a rejected pack operation."""


@dataclass(frozen=True, slots=True)
class PackSummary:
    id: int
    name: str
    video_count: int


def _summary_query():
    count = (
        select(func.count())
        .where(MediaPackVideo.pack_id == MediaPack.id)
        .correlate(MediaPack)
        .scalar_subquery()
    )
    return select(MediaPack.id, MediaPack.name, count)


def get_pack(session: Session, pack_id: int) -> PackSummary:
    row = session.execute(_summary_query().where(MediaPack.id == pack_id)).one_or_none()
    if row is None:
        raise PackError("PACK_NOT_FOUND")
    return PackSummary(*row)


def list_packs(
    session: Session,
    *,
    digital_human_id: int | None,
    exclude_digital_human_id: int | None,
    after: int | None,
    limit: int,
) -> tuple[list[PackSummary], int]:
    filters = []
    if digital_human_id is not None:
        if (
            session.scalar(
                select(DigitalHuman.id).where(
                    DigitalHuman.id == digital_human_id,
                    DigitalHuman.deleted_at.is_(None),
                )
            )
            is None
        ):
            raise PackError("HUMAN_NOT_FOUND")
        filters.append(
            MediaPack.id.in_(
                select(DigitalHumanPack.pack_id).where(
                    DigitalHumanPack.digital_human_id == digital_human_id
                )
            )
        )
    if exclude_digital_human_id is not None:
        filters.append(
            MediaPack.id.not_in(
                select(DigitalHumanPack.pack_id).where(
                    DigitalHumanPack.digital_human_id == exclude_digital_human_id
                )
            )
        )
    total = session.scalar(select(func.count()).select_from(MediaPack).where(*filters))
    query = _summary_query().where(*filters)
    if after is not None:
        query = query.where(MediaPack.id < after)
    rows = session.execute(query.order_by(MediaPack.id.desc()).limit(limit))
    return [PackSummary(*row) for row in rows], total or 0


def _lock_human(session: Session, human_id: int) -> None:
    if (
        session.scalar(
            select(DigitalHuman.id)
            .where(DigitalHuman.id == human_id, DigitalHuman.deleted_at.is_(None))
            .with_for_update()
        )
        is None
    ):
        raise PackError("HUMAN_NOT_FOUND")


def _lock_pack(session: Session, pack_id: int) -> None:
    if (
        session.scalar(
            select(MediaPack.id).where(MediaPack.id == pack_id).with_for_update()
        )
        is None
    ):
        raise PackError("PACK_NOT_FOUND")


def _add_videos(session: Session, pack_id: int, asset_ids: list[int]) -> None:
    ids = list(dict.fromkeys(asset_ids))
    # Share the asset row lock with direct persona links and asset deletion.
    ready = session.scalars(
        select(MediaAsset.id)
        .where(
            MediaAsset.id.in_(ids),
            MediaAsset.kind == "video",
            MediaAsset.status == "ready",
            MediaAsset.deleted_at.is_(None),
            MediaAsset.is_portrait.is_(False),
        )
        .order_by(MediaAsset.id)
        .with_for_update(read=True)
    ).all()
    if len(ready) != len(ids):
        raise PackError("PACK_REQUIRES_READY_VIDEOS")
    session.execute(
        insert(MediaPackVideo)
        .values([{"pack_id": pack_id, "asset_id": asset_id} for asset_id in ids])
        .on_conflict_do_nothing(index_elements=["pack_id", "asset_id"])
    )


def _link_pack(session: Session, human_id: int, pack_id: int) -> None:
    session.execute(
        insert(DigitalHumanPack)
        .values(digital_human_id=human_id, pack_id=pack_id)
        .on_conflict_do_nothing(index_elements=["digital_human_id", "pack_id"])
    )


def create_pack(
    session: Session, name: str, token: UUID, asset_ids: list[int], human_id: int | None
) -> PackSummary:
    if human_id is not None:
        _lock_human(session, human_id)
    pack_id = session.scalar(
        insert(MediaPack)
        .values(name=name, creation_token=token)
        .on_conflict_do_nothing(index_elements=["creation_token"])
        .returning(MediaPack.id)
    )
    if pack_id is None:
        existing = session.execute(
            select(MediaPack.id).where(MediaPack.creation_token == token)
        ).scalar_one()
        return get_pack(session, existing)
    _add_videos(session, pack_id, asset_ids)
    if human_id is not None:
        _link_pack(session, human_id, pack_id)
    result = get_pack(session, pack_id)
    session.commit()
    return result


def add_videos(session: Session, pack_id: int, asset_ids: list[int]) -> PackSummary:
    _lock_pack(session, pack_id)
    _add_videos(session, pack_id, asset_ids)
    result = get_pack(session, pack_id)
    session.commit()
    return result


def remove_video(session: Session, pack_id: int, asset_id: int) -> None:
    _lock_pack(session, pack_id)
    session.execute(
        delete(MediaPackVideo).where(
            MediaPackVideo.pack_id == pack_id, MediaPackVideo.asset_id == asset_id
        )
    )
    session.commit()


def attach_pack(session: Session, human_id: int, pack_id: int) -> PackSummary:
    _lock_human(session, human_id)
    pack = get_pack(session, pack_id)
    _link_pack(session, human_id, pack_id)
    session.commit()
    return pack


def detach_pack(session: Session, human_id: int, pack_id: int) -> None:
    _lock_human(session, human_id)
    session.execute(
        delete(DigitalHumanPack).where(
            DigitalHumanPack.digital_human_id == human_id,
            DigitalHumanPack.pack_id == pack_id,
        )
    )
    session.commit()


def packs_for_video(session: Session, asset_id: int) -> list[tuple[int, str]]:
    return [
        (pack_id, name)
        for pack_id, name in session.execute(
            select(MediaPack.id, MediaPack.name)
            .join(MediaPackVideo, MediaPackVideo.pack_id == MediaPack.id)
            .where(MediaPackVideo.asset_id == asset_id)
            .order_by(MediaPack.id.desc())
        )
    ]
