from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.reporting.focused_spus import (
    FocusedSpuNotFoundInShop,
    FocusedSpuPatch,
    FocusedSpuQuery,
    apply_patch,
    list_focused_spus,
)

pytestmark = [pytest.mark.domain_reporting, pytest.mark.layer_integration]


def _seed_shop(session: Session, suffix: str) -> int:
    return int(
        session.execute(
            text(
                "INSERT INTO commerce.shops "
                "(platform, shop_id, account_name, status) "
                "VALUES ('tiktok', :shop_id, :name, 'active') RETURNING id"
            ),
            {
                "shop_id": f"TEST_FOCUSED_MODULE_{suffix}",
                "name": f"TEST_FOCUSED_MODULE_{suffix}",
            },
        ).scalar_one()
    )


def _seed_products(session: Session, shop_pk: int, count: int) -> tuple[str, ...]:
    spu_ids = tuple(f"TEST_FOCUSED_MODULE_{index:03d}" for index in range(count))
    session.execute(
        text(
            "INSERT INTO commerce.products_spu (shop_pk, spu_id, title, status) "
            "SELECT :shop_pk, value, 'TEST Focused ' || value, 'ACTIVATE' "
            "FROM unnest(CAST(:spu_ids AS text[])) AS value"
        ),
        {"shop_pk": shop_pk, "spu_ids": list(spu_ids)},
    )
    return spu_ids


def test_focused_spu_module_supports_unbounded_collection_and_soft_restore(
    db_session: Session,
) -> None:
    shop_pk = _seed_shop(db_session, "A")
    spu_ids = _seed_products(db_session, shop_pk, 101)

    added = apply_patch(
        db_session,
        shop_pk=shop_pk,
        patch=FocusedSpuPatch(add_spu_ids=spu_ids),
        actor="TEST_actor",
    )
    assert added.total == 101
    first_page = list_focused_spus(
        db_session,
        shop_pk=shop_pk,
        query=FocusedSpuQuery(limit=50),
    )
    assert first_page.total == 101
    assert first_page.matched_total == 101
    assert len(first_page.items) == 50

    removed_id = spu_ids[0]
    removed = apply_patch(
        db_session,
        shop_pk=shop_pk,
        patch=FocusedSpuPatch(remove_spu_ids=(removed_id,)),
        actor="TEST_actor",
    )
    assert removed.total == 100
    inactive = db_session.execute(
        text(
            "SELECT active, removed_at FROM reporting.focused_spus "
            "WHERE shop_pk=:shop_pk AND spu_id=:spu_id"
        ),
        {"shop_pk": shop_pk, "spu_id": removed_id},
    ).one()
    assert inactive.active is False
    assert inactive.removed_at is not None

    restored = apply_patch(
        db_session,
        shop_pk=shop_pk,
        patch=FocusedSpuPatch(add_spu_ids=(removed_id,)),
        actor="TEST_actor",
    )
    assert restored.total == 101
    active = db_session.execute(
        text(
            "SELECT active, removed_at FROM reporting.focused_spus "
            "WHERE shop_pk=:shop_pk AND spu_id=:spu_id"
        ),
        {"shop_pk": shop_pk, "spu_id": removed_id},
    ).one()
    assert active.active is True
    assert active.removed_at is None


def test_focused_spu_module_rejects_foreign_shop_batch_atomically(
    db_session: Session,
) -> None:
    shop_a = _seed_shop(db_session, "ATOMIC_A")
    shop_b = _seed_shop(db_session, "ATOMIC_B")
    own_id = _seed_products(db_session, shop_a, 1)[0]
    foreign_id = "TEST_FOCUSED_FOREIGN"
    db_session.execute(
        text(
            "INSERT INTO commerce.products_spu (shop_pk, spu_id, title, status) "
            "VALUES (:shop_pk, :spu_id, 'TEST Foreign', 'ACTIVATE')"
        ),
        {"shop_pk": shop_b, "spu_id": foreign_id},
    )

    with pytest.raises(FocusedSpuNotFoundInShop):
        apply_patch(
            db_session,
            shop_pk=shop_a,
            patch=FocusedSpuPatch(add_spu_ids=(own_id, foreign_id)),
            actor="TEST_actor",
        )

    page = list_focused_spus(
        db_session,
        shop_pk=shop_a,
        query=FocusedSpuQuery(),
    )
    assert page.total == 0
