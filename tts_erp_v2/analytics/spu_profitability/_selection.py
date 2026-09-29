"""Private SPU-selection relation for the profitability implementation."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_profitability._types import (
    ExactIdsSelection,
    FocusedSelection,
    SpuSelection,
)

_SQL_SELECTED_CATALOG = text(
    """
    SELECT cp.id AS spu_pk, cp.shop_pk, cp.spu_id, cp.title, cp.status,
           cp.main_image_url, s.shop_id, s.account_name AS shop_name
    FROM commerce.products_spu cp
    LEFT JOIN commerce.shops s ON s.id = cp.shop_pk
    WHERE (CAST(:shop_pk AS bigint) IS NULL
           OR cp.shop_pk = CAST(:shop_pk AS bigint))
      AND (CAST(:q AS text) IS NULL OR cp.spu_id ILIKE '%' || :q || '%')
      AND (CAST(:spu_ids AS text[]) IS NULL
           OR cp.spu_id = ANY(CAST(:spu_ids AS text[])))
      AND (CAST(:focused_scope AS boolean) IS NOT TRUE
           OR EXISTS (
               SELECT 1 FROM reporting.focused_spus fs
               WHERE fs.shop_pk = cp.shop_pk
                 AND fs.spu_id = cp.spu_id
                 AND fs.active IS TRUE
           ))
      AND (CAST(:active_only AS boolean) IS NOT TRUE
           OR cp.status ILIKE 'activate')
    ORDER BY cp.id
    """
)


def resolve_selected_spus(
    session: Session,
    *,
    selection: SpuSelection,
    shop_pk: int | None,
    catalog_search: str | None,
    active_only: bool,
) -> list[Any]:
    """Resolve one selection into catalog rows inside the caller's snapshot."""

    spu_ids = selection.spu_ids if isinstance(selection, ExactIdsSelection) else None
    return list(
        session.execute(
            _SQL_SELECTED_CATALOG,
            {
                "shop_pk": shop_pk,
                "q": catalog_search,
                "spu_ids": list(spu_ids) if spu_ids is not None else None,
                "focused_scope": isinstance(selection, FocusedSelection),
                "active_only": active_only,
            },
        )
        .mappings()
        .all()
    )
