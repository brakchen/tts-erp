"""HTTP endpoints for shared video packs within the existing media API."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session, require_cookie_csrf, require_role_at_least
from tts_erp_v2.media import packs

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
RecordId = Annotated[int, Field(ge=1)]


class PackOut(BaseModel):
    pack_id: int = Field(serialization_alias="packId")
    name: str
    video_count: int = Field(serialization_alias="videoCount")


class PackPageOut(BaseModel):
    items: list[PackOut]
    total: int
    next_cursor: str | None = Field(serialization_alias="nextCursor")


class PackVideosIn(BaseModel):
    asset_ids: list[RecordId] = Field(alias="assetIds", min_length=1, max_length=200)


class PackCreateIn(PackVideosIn):
    name: str = Field(min_length=1, max_length=80)
    creation_token: UUID = Field(alias="creationToken")
    digital_human_id: int | None = Field(default=None, alias="digitalHumanId", ge=1)


class PackLinkIn(BaseModel):
    pack_id: int = Field(alias="packId", ge=1)


def _out(pack: packs.PackSummary) -> PackOut:
    return PackOut(pack_id=pack.id, name=pack.name, video_count=pack.video_count)


def _read(request: Request) -> None:
    require_role_at_least(request, "readonly")


def _write(request: Request) -> None:
    require_role_at_least(request, "readwrite")
    require_cookie_csrf(request)


@contextmanager
def _operation(session: Session) -> Iterator[None]:
    try:
        yield
    except packs.PackError as error:
        session.rollback()
        code = str(error)
        raise HTTPException(
            404 if code.endswith("_NOT_FOUND") else 422, code
        ) from error


@router.get("/packs", dependencies=[Depends(_read)])
def list_packs(
    session: SessionDep,
    digital_human_id: int | None = Query(default=None, alias="digitalHumanId", ge=1),
    exclude_digital_human_id: int | None = Query(
        default=None, alias="excludeDigitalHumanId", ge=1
    ),
    cursor: str = Query(default="", max_length=19),
    limit: int = Query(default=12, ge=1, le=200),
) -> PackPageOut:
    if cursor and (
        not cursor.isascii() or not cursor.isdecimal() or not 0 < int(cursor) < 2**63
    ):
        raise HTTPException(400, "INVALID_CURSOR")
    with _operation(session):
        rows, total = packs.list_packs(
            session,
            digital_human_id=digital_human_id,
            exclude_digital_human_id=exclude_digital_human_id,
            after=int(cursor) if cursor else None,
            limit=limit + 1,
        )
    return PackPageOut(
        items=[_out(row) for row in rows[:limit]],
        total=total,
        next_cursor=str(rows[limit - 1].id) if len(rows) > limit else None,
    )


@router.get("/packs/{pack_id}", dependencies=[Depends(_read)])
def get_pack(pack_id: int, session: SessionDep) -> PackOut:
    with _operation(session):
        return _out(packs.get_pack(session, pack_id))


@router.post("/packs", status_code=201, dependencies=[Depends(_write)])
def create_pack(body: PackCreateIn, session: SessionDep) -> PackOut:
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "PACK_NAME_REQUIRED")
    with _operation(session):
        return _out(
            packs.create_pack(
                session,
                name,
                body.creation_token,
                body.asset_ids,
                body.digital_human_id,
            )
        )


@router.post("/packs/{pack_id}/videos", dependencies=[Depends(_write)])
def add_videos(pack_id: int, body: PackVideosIn, session: SessionDep) -> PackOut:
    with _operation(session):
        return _out(packs.add_videos(session, pack_id, body.asset_ids))


@router.delete(
    "/packs/{pack_id}/videos/{asset_id}",
    status_code=204,
    dependencies=[Depends(_write)],
)
def remove_video(pack_id: int, asset_id: int, session: SessionDep) -> Response:
    with _operation(session):
        packs.remove_video(session, pack_id, asset_id)
    return Response(status_code=204)


@router.post("/digital-humans/{digital_human_id}/packs", dependencies=[Depends(_write)])
def attach_pack(
    digital_human_id: int, body: PackLinkIn, session: SessionDep
) -> PackOut:
    with _operation(session):
        return _out(packs.attach_pack(session, digital_human_id, body.pack_id))


@router.delete(
    "/digital-humans/{digital_human_id}/packs/{pack_id}",
    status_code=204,
    dependencies=[Depends(_write)],
)
def detach_pack(digital_human_id: int, pack_id: int, session: SessionDep) -> Response:
    with _operation(session):
        packs.detach_pack(session, digital_human_id, pack_id)
    return Response(status_code=204)
