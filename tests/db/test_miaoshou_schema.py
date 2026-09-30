"""Schema contract for source-owned Miaoshou package tables."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from tts_erp_v2.db.models.miaoshou import (
    MiaoshouPackage,
    MiaoshouPackageGiftItem,
    MiaoshouPackageItem,
    MiaoshouPackageRawRecord,
    MiaoshouPurchaseOrderRawRecord,
    MiaoshouPurchasePrice,
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
        "purchase_order_raw_records",
        "purchase_prices",
    }.issubset(set(inspector.get_table_names(schema="miaoshou")))


def test_miaoshou_models_are_schema_owned() -> None:
    assert MiaoshouPackageRawRecord.__table__.schema == "miaoshou"
    assert MiaoshouPackage.__table__.schema == "miaoshou"
    assert MiaoshouPackageItem.__table__.schema == "miaoshou"
    assert MiaoshouPackageGiftItem.__table__.schema == "miaoshou"
    assert MiaoshouPurchaseOrderRawRecord.__table__.schema == "miaoshou"
    assert MiaoshouPurchasePrice.__table__.schema == "miaoshou"
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


def test_migration_relocates_history_and_preserves_partial_snapshots(db_engine) -> None:
    migration_path = (
        Path(__file__).parents[2] / "alembic/versions/0045_miaoshou_package_schema.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0045_test", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    full_payload = {
        "opOrderPackageId": "TEST_MIGRATION_PACKAGE",
        "platform": "tiktok",
        "site": "VN",
        "appPackageStatus": "wait_seller_send",
        "orderInfo": {
            "platformOrderSn": "TEST_MIGRATION_ORDER",
            "gmtOrderStart": "2026-09-30 09:00:00",
            "gmtOrderModified": "2026-09-30 10:00:00",
            "gmtDelivery": "2026-09-30 11:00:00",
        },
        "items": [
            {
                "opOrderPackageItemId": "TEST_MIGRATION_ITEM",
                "platformItemId": "TEST_SPU",
                "quantity": "1",
            }
        ],
    }
    partial_payload = {
        "opOrderPackageId": "TEST_MIGRATION_PACKAGE",
        "appPackageStatus": "finished",
        "orderInfo": {"gmtOrderModified": "2026-09-30 12:00:00"},
    }
    orphan_payload = {"opOrderPackageId": "TEST_ORPHAN_PACKAGE", "items": []}

    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            assert (
                conn.execute(text("select current_database()")).scalar_one()
                == "tts_erp_v3_test"
            )
            conn.execute(text("DROP SCHEMA miaoshou CASCADE"))
            credential_id = conn.execute(
                text(
                    "INSERT INTO integration.credentials "
                    "(provider, external_account_id, account_label, ciphertext) "
                    "VALUES ('miaoshou', 'TEST_MIGRATION_LICENSE', "
                    "'TEST migration', decode('00','hex')) RETURNING id"
                )
            ).scalar_one()
            shop_pk = conn.execute(
                text(
                    "INSERT INTO commerce.shops "
                    "(platform, shop_id, account_name, status) VALUES "
                    "('tiktok', 'TEST_MIGRATION_SHOP', 'TEST migration', 'active') "
                    "RETURNING id"
                )
            ).scalar_one()
            order_pk = conn.execute(
                text(
                    "INSERT INTO commerce.sales_orders (shop_pk, order_id, status) "
                    "VALUES (:shop_pk, 'TEST_MIGRATION_ORDER', 'finished') RETURNING id"
                ),
                {"shop_pk": shop_pk},
            ).scalar_one()
            full_raw_id = conn.execute(
                text(
                    "INSERT INTO integration.raw_records "
                    "(credential_id, endpoint, external_id, captured_at, payload, payload_hash) "
                    "VALUES (:credential_id, 'miaoshou.package.search_package_list', "
                    "'TEST_MIGRATION_PACKAGE', :captured_at, cast(:payload as jsonb), "
                    "'TEST_FULL_HASH') RETURNING id"
                ),
                {
                    "credential_id": credential_id,
                    "captured_at": datetime(2026, 9, 30, 2, 0, tzinfo=UTC),
                    "payload": json.dumps(full_payload),
                },
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO integration.raw_records "
                    "(credential_id, endpoint, external_id, captured_at, payload, payload_hash) "
                    "VALUES (:credential_id, 'miaoshou.package.get_package_info', "
                    "'TEST_MIGRATION_PACKAGE', :captured_at, cast(:payload as jsonb), "
                    "'TEST_PARTIAL_HASH')"
                ),
                {
                    "credential_id": credential_id,
                    "captured_at": datetime(2026, 9, 30, 4, 0, tzinfo=UTC),
                    "payload": json.dumps(partial_payload),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO integration.raw_records "
                    "(credential_id, endpoint, external_id, captured_at, payload, payload_hash) "
                    "VALUES (NULL, 'miaoshou.package.search_package_list', "
                    "'TEST_ORPHAN_PACKAGE', :captured_at, cast(:payload as jsonb), "
                    "'TEST_ORPHAN_HASH')"
                ),
                {
                    "captured_at": datetime(2026, 9, 30, 3, 0, tzinfo=UTC),
                    "payload": json.dumps(orphan_payload),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO fulfillment.shipments "
                    "(order_pk, external_package_id, status, raw_record_id) "
                    "VALUES (:order_pk, 'TEST_MIGRATION_PACKAGE', 'finished', :raw_id)"
                ),
                {"order_pk": order_pk, "raw_id": full_raw_id},
            )

            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            migration.upgrade()

            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM miaoshou.package_raw_records "
                        "WHERE external_package_id LIKE 'TEST_%PACKAGE'"
                    )
                ).scalar_one()
                == 3
            )
            row = (
                conn.execute(
                    text(
                        "SELECT platform, app_package_status, source_created_at, "
                        "source_updated_at, shipped_at FROM miaoshou.packages "
                        "WHERE external_package_id='TEST_MIGRATION_PACKAGE'"
                    )
                )
                .mappings()
                .one()
            )
            assert row["platform"] == "tiktok"
            assert row["app_package_status"] == "finished"
            assert row["source_created_at"] == datetime(2026, 9, 30, 1, 0, tzinfo=UTC)
            assert row["source_updated_at"] == datetime(2026, 9, 30, 4, 0, tzinfo=UTC)
            assert row["shipped_at"] == datetime(2026, 9, 30, 3, 0, tzinfo=UTC)
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM miaoshou.package_items i "
                        "JOIN miaoshou.packages p ON p.id=i.package_id "
                        "WHERE p.external_package_id='TEST_MIGRATION_PACKAGE'"
                    )
                ).scalar_one()
                == 1
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM integration.raw_records "
                        "WHERE external_id IN ('TEST_MIGRATION_PACKAGE', "
                        "'TEST_ORPHAN_PACKAGE')"
                    )
                ).scalar_one()
                == 0
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM fulfillment.shipments "
                        "WHERE external_package_id='TEST_MIGRATION_PACKAGE'"
                    )
                ).scalar_one()
                == 0
            )
        finally:
            transaction.rollback()
