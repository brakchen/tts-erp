"""Schema contract for source-owned Miaoshou package tables."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from tts_erp_v2.db.models.miaoshou import (
    MiaoshouPackage,
    MiaoshouPackageGiftItem,
    MiaoshouPackageItem,
    MiaoshouPackageRawRecord,
    MiaoshouSyncCursor,
    MiaoshouSyncIssue,
)

pytestmark = [pytest.mark.domain_miaoshou, pytest.mark.layer_integration]


def test_miaoshou_schema_contains_package_domain_tables(db_engine) -> None:
    inspector = inspect(db_engine)
    assert "miaoshou" in inspector.get_schema_names()
    assert {
        "package_raw_records",
        "packages",
        "package_items",
        "package_gift_items",
        "sync_cursors",
        "sync_issues",
    }.issubset(set(inspector.get_table_names(schema="miaoshou")))


def test_miaoshou_models_are_schema_owned() -> None:
    assert MiaoshouPackageRawRecord.__table__.schema == "miaoshou"
    assert MiaoshouPackage.__table__.schema == "miaoshou"
    assert MiaoshouPackageItem.__table__.schema == "miaoshou"
    assert MiaoshouPackageGiftItem.__table__.schema == "miaoshou"
    assert MiaoshouSyncCursor.__table__.schema == "miaoshou"
    assert MiaoshouSyncIssue.__table__.schema == "miaoshou"


def test_package_fk_stays_inside_miaoshou_schema(db_engine) -> None:
    inspector = inspect(db_engine)
    package_fks = inspector.get_foreign_keys("packages", schema="miaoshou")
    raw_fk = next(
        fk for fk in package_fks if fk["constrained_columns"] == ["raw_record_id"]
    )
    assert raw_fk["referred_schema"] == "miaoshou"
    assert raw_fk["referred_table"] == "package_raw_records"

    item_fk = inspector.get_foreign_keys("package_items", schema="miaoshou")[0]
    assert item_fk["referred_schema"] == "miaoshou"
    assert item_fk["referred_table"] == "packages"
