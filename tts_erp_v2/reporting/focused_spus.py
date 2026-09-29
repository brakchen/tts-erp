"""Shop-scoped focused-SPU persistence.

The module hides SQL, validation, stable pagination, and atomic delta semantics
from HTTP adapters.  It deliberately stores current membership state rather
than a complete event history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

_MAX_PATCH_IDS = 500
_MAX_SPU_ID_LENGTH = 128


class FocusedSpuError(ValueError):
    """Base class for focused-SPU domain validation errors."""


class FocusedShopNotFound(FocusedSpuError):
    """The requested internal shop primary key does not exist."""


class FocusedSpuPatchConflict(FocusedSpuError):
    """An SPU occurs in both add and remove sets."""


class FocusedSpuPatchTooLarge(FocusedSpuError):
    """One mutation request exceeds the bounded batch size."""


class FocusedSpuNotFoundInShop(FocusedSpuError):
    """At least one requested addition is not owned by the target shop."""

    def __init__(self, spu_ids: tuple[str, ...]) -> None:
        self.spu_ids = spu_ids
        super().__init__(f"SPUs not found in shop: {', '.join(spu_ids)}")


@dataclass(frozen=True, slots=True)
class FocusedSpuQuery:
    search: str | None = None
    limit: int = 50
    offset: int = 0

    def __post_init__(self) -> None:
        if self.search is not None and len(self.search) > 200:
            raise FocusedSpuError("search must contain at most 200 characters")
        if self.limit < 1 or self.limit > 500:
            raise FocusedSpuError("limit must be between 1 and 500")
        if self.offset < 0:
            raise FocusedSpuError("offset must be >= 0")


@dataclass(frozen=True, slots=True)
class FocusedSpuPatch:
    add_spu_ids: tuple[str, ...] = ()
    remove_spu_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FocusedSpuItem:
    spu_pk: int
    spu_id: str
    title: str | None
    status: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class FocusedSpuPage:
    shop_pk: int
    items: tuple[FocusedSpuItem, ...]
    total: int
    matched_total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class FocusedSpuPatchResult:
    shop_pk: int
    total: int
    added_spu_ids: tuple[str, ...]
    removed_spu_ids: tuple[str, ...]


_SQL_SHOP_EXISTS = text("SELECT 1 FROM commerce.shops WHERE id = :shop_pk")
_SQL_PRODUCT_IDS = text(
    "SELECT spu_id FROM commerce.products_spu "
    "WHERE shop_pk = :shop_pk AND spu_id = ANY(CAST(:spu_ids AS text[]))"
)
_SQL_TOTAL = text(
    "SELECT count(*)::int FROM reporting.focused_spus "
    "WHERE shop_pk = :shop_pk AND active IS TRUE"
)
_SQL_MATCHED_TOTAL = text(
    """
    SELECT count(*)::int
    FROM reporting.focused_spus fs
    JOIN commerce.products_spu cp
      ON cp.shop_pk = fs.shop_pk AND cp.spu_id = fs.spu_id
    WHERE fs.shop_pk = :shop_pk
      AND fs.active IS TRUE
      AND (CAST(:q AS text) IS NULL
           OR fs.spu_id ILIKE '%' || CAST(:q AS text) || '%'
           OR coalesce(cp.title, '') ILIKE '%' || CAST(:q AS text) || '%')
    """
)
_SQL_LIST = text(
    """
    SELECT cp.id AS spu_pk, fs.spu_id, cp.title, cp.status,
           fs.created_at, fs.updated_at
    FROM reporting.focused_spus fs
    JOIN commerce.products_spu cp
      ON cp.shop_pk = fs.shop_pk AND cp.spu_id = fs.spu_id
    WHERE fs.shop_pk = :shop_pk
      AND fs.active IS TRUE
      AND (CAST(:q AS text) IS NULL
           OR fs.spu_id ILIKE '%' || CAST(:q AS text) || '%'
           OR coalesce(cp.title, '') ILIKE '%' || CAST(:q AS text) || '%')
    ORDER BY fs.updated_at DESC, fs.spu_id ASC
    LIMIT :limit OFFSET :offset
    """
)
_SQL_REMOVE = text(
    """
    UPDATE reporting.focused_spus
    SET active = FALSE,
        removed_by = :actor,
        removed_at = now()
    WHERE shop_pk = :shop_pk
      AND spu_id = ANY(CAST(:spu_ids AS text[]))
      AND active IS TRUE
    """
)
_SQL_ADD = text(
    """
    INSERT INTO reporting.focused_spus (
        shop_pk, spu_id, active, added_by, removed_by, removed_at
    )
    SELECT :shop_pk, ids.spu_id, TRUE, :actor, NULL, NULL
    FROM unnest(CAST(:spu_ids AS text[])) AS ids(spu_id)
    ON CONFLICT (shop_pk, spu_id) DO UPDATE
    SET active = TRUE,
        added_by = EXCLUDED.added_by,
        removed_by = NULL,
        removed_at = NULL
    """
)


def _require_shop(session: Session, shop_pk: int) -> None:
    if shop_pk < 1 or session.execute(_SQL_SHOP_EXISTS, {"shop_pk": shop_pk}).first() is None:
        raise FocusedShopNotFound(f"shop {shop_pk} not found")


def _normalize_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
    if any(len(value) > _MAX_SPU_ID_LENGTH for value in normalized):
        raise FocusedSpuError("each spu_id must contain between 1 and 128 characters")
    return normalized


def list_focused_spus(
    session: Session,
    *,
    shop_pk: int,
    query: FocusedSpuQuery,
) -> FocusedSpuPage:
    """Return one stable management page and both scoped counts."""

    _require_shop(session, shop_pk)
    search = query.search.strip() if query.search and query.search.strip() else None
    params = {
        "shop_pk": shop_pk,
        "q": search,
        "limit": query.limit,
        "offset": query.offset,
    }
    total = int(session.execute(_SQL_TOTAL, params).scalar_one())
    matched_total = int(session.execute(_SQL_MATCHED_TOTAL, params).scalar_one())
    rows = session.execute(_SQL_LIST, params).mappings().all()
    return FocusedSpuPage(
        shop_pk=shop_pk,
        items=tuple(
            FocusedSpuItem(
                spu_pk=int(row["spu_pk"]),
                spu_id=str(row["spu_id"]),
                title=row["title"],
                status=row["status"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ),
        total=total,
        matched_total=matched_total,
        limit=query.limit,
        offset=query.offset,
    )


def apply_patch(
    session: Session,
    *,
    shop_pk: int,
    patch: FocusedSpuPatch,
    actor: str | None,
) -> FocusedSpuPatchResult:
    """Validate and apply one atomic add/remove delta.

    The caller owns commit/rollback. All additions are validated before either
    mutation statement runs, so invalid mixed batches cannot partially apply.
    """

    _require_shop(session, shop_pk)
    add_ids = _normalize_ids(patch.add_spu_ids)
    remove_ids = _normalize_ids(patch.remove_spu_ids)
    if len(add_ids) + len(remove_ids) > _MAX_PATCH_IDS:
        raise FocusedSpuPatchTooLarge(
            f"patch must contain at most {_MAX_PATCH_IDS} SPU ids"
        )
    overlap = tuple(sorted(set(add_ids).intersection(remove_ids)))
    if overlap:
        raise FocusedSpuPatchConflict(
            f"SPUs cannot be both added and removed: {', '.join(overlap)}"
        )

    if add_ids:
        found = {
            str(value)
            for value in session.execute(
                _SQL_PRODUCT_IDS,
                {"shop_pk": shop_pk, "spu_ids": list(add_ids)},
            ).scalars()
        }
        missing = tuple(value for value in add_ids if value not in found)
        if missing:
            raise FocusedSpuNotFoundInShop(missing)

    params = {"shop_pk": shop_pk, "actor": actor}
    if remove_ids:
        session.execute(_SQL_REMOVE, {**params, "spu_ids": list(remove_ids)})
    if add_ids:
        session.execute(_SQL_ADD, {**params, "spu_ids": list(add_ids)})
    total = int(session.execute(_SQL_TOTAL, {"shop_pk": shop_pk}).scalar_one())
    return FocusedSpuPatchResult(
        shop_pk=shop_pk,
        total=total,
        added_spu_ids=add_ids,
        removed_spu_ids=remove_ids,
    )
