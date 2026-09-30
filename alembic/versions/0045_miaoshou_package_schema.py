"""Create source-owned ``miaoshou`` schema and relocate package data.

Revision ID: 0045_miaoshou_package_schema
Revises: 0044_drop_linkage_schema
Create Date: 2026-09-30

The first package integration incorrectly projected Miaoshou payloads into
``fulfillment.shipments`` and stored source payload/cursor/issues in generic
``integration`` tables. This migration atomically copies that data into
``miaoshou.*`` and removes only rows whose provenance is the Miaoshou package
endpoints. Production execution remains human-operated and guarded by the
repository Alembic launcher.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0045_miaoshou_package_schema"
down_revision: str | None = "0044_drop_linkage_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "synced_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS miaoshou")

    op.create_table(
        "package_raw_records",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("credential_id", sa.BigInteger),
        sa.Column("external_package_id", sa.Text),
        sa.Column("endpoint", sa.Text, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("payload_hash", sa.Text, nullable=False),
        sa.Column(
            "captured_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["integration.credentials.id"], ondelete="SET NULL"
        ),
        schema="miaoshou",
    )
    op.create_index(
        "ix_miaoshou_package_raw_external",
        "package_raw_records",
        ["credential_id", "external_package_id"],
        schema="miaoshou",
    )
    op.create_index(
        "ix_miaoshou_package_raw_captured",
        "package_raw_records",
        ["captured_at"],
        schema="miaoshou",
    )

    op.create_table(
        "packages",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("credential_id", sa.BigInteger, nullable=False),
        sa.Column("external_package_id", sa.Text, nullable=False),
        sa.Column("raw_record_id", sa.BigInteger, nullable=False),
        sa.Column("source_endpoint", sa.Text, nullable=False),
        sa.Column("platform", sa.Text),
        sa.Column("site", sa.Text),
        sa.Column("shop_id", sa.Text),
        sa.Column("shop_name", sa.Text),
        sa.Column("shop_nick", sa.Text),
        sa.Column("app_package_no", sa.Text),
        sa.Column("app_package_status", sa.Text),
        sa.Column("app_package_status_text", sa.Text),
        sa.Column("platform_package_status", sa.Text),
        sa.Column("fulfillment_type", sa.Text),
        sa.Column("platform_order_sn", sa.Text),
        sa.Column("platform_order_status", sa.Text),
        sa.Column("currency", sa.Text),
        sa.Column("logistics_no", sa.Text),
        sa.Column("logistics_company", sa.Text),
        sa.Column("logistics_product_id", sa.Text),
        sa.Column("logistics_product_name", sa.Text),
        sa.Column("source_created_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("source_updated_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("shipped_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("order_info", postgresql.JSONB),
        sa.Column("consignee_info", postgresql.JSONB),
        sa.Column("logistics_info", postgresql.JSONB),
        sa.Column("last_mile_info", postgresql.JSONB),
        sa.Column("raw_payload", postgresql.JSONB, nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["integration.credentials.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["raw_record_id"],
            ["miaoshou.package_raw_records.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "credential_id",
            "external_package_id",
            name="uq_miaoshou_packages_credential_external",
        ),
        schema="miaoshou",
    )
    op.create_index(
        "ix_miaoshou_packages_order",
        "packages",
        ["platform_order_sn"],
        schema="miaoshou",
    )
    op.create_index(
        "ix_miaoshou_packages_status",
        "packages",
        ["app_package_status"],
        schema="miaoshou",
    )
    op.create_index(
        "ix_miaoshou_packages_source_updated",
        "packages",
        ["source_updated_at"],
        schema="miaoshou",
    )

    op.create_table(
        "package_items",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("package_id", sa.BigInteger, nullable=False),
        sa.Column("external_package_item_id", sa.Text, nullable=False),
        sa.Column("external_order_item_id", sa.Text),
        sa.Column("platform_order_item_index", sa.Text),
        sa.Column("platform_product_id", sa.Text),
        sa.Column("platform_sku_id", sa.Text),
        sa.Column("platform_item_num", sa.Text),
        sa.Column("platform_outer_sku_id", sa.Text),
        sa.Column("title", sa.Text),
        sa.Column("sku_name", sa.Text),
        sa.Column("quantity", sa.Numeric(20, 4)),
        sa.Column("original_price", sa.Numeric(20, 4)),
        sa.Column("discounted_price", sa.Numeric(20, 4)),
        sa.Column("image_url", sa.Text),
        sa.Column("original_image_url", sa.Text),
        sa.Column("raw_payload", postgresql.JSONB, nullable=False),
        sa.Column("active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("removed_at", sa.TIMESTAMP(timezone=True)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["package_id"], ["miaoshou.packages.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "package_id",
            "external_package_item_id",
            name="uq_miaoshou_package_items_package_external",
        ),
        schema="miaoshou",
    )
    op.create_index(
        "ix_miaoshou_package_items_product",
        "package_items",
        ["platform_product_id"],
        schema="miaoshou",
    )
    op.create_index(
        "ix_miaoshou_package_items_sku",
        "package_items",
        ["platform_sku_id"],
        schema="miaoshou",
    )

    op.create_table(
        "package_gift_items",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("package_id", sa.BigInteger, nullable=False),
        sa.Column("external_gift_item_id", sa.Text, nullable=False),
        sa.Column("goods_id", sa.Text),
        sa.Column("goods_sku_id", sa.Text),
        sa.Column("goods_name", sa.Text),
        sa.Column("item_num", sa.Text),
        sa.Column("sku_name", sa.Text),
        sa.Column("goods_sku_outer_id", sa.Text),
        sa.Column("quantity", sa.Numeric(20, 4)),
        sa.Column("original_price", sa.Numeric(20, 4)),
        sa.Column("discounted_price", sa.Numeric(20, 4)),
        sa.Column("image_url", sa.Text),
        sa.Column("raw_payload", postgresql.JSONB, nullable=False),
        sa.Column("active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("removed_at", sa.TIMESTAMP(timezone=True)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["package_id"], ["miaoshou.packages.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "package_id",
            "external_gift_item_id",
            name="uq_miaoshou_package_gifts_package_external",
        ),
        schema="miaoshou",
    )

    op.create_table(
        "sync_cursors",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("credential_id", sa.BigInteger, nullable=False),
        sa.Column("resource", sa.Text, nullable=False),
        sa.Column("cursor_value", sa.Text),
        sa.Column("cursor_epoch_ms", sa.BigInteger),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["integration.credentials.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "credential_id",
            "resource",
            name="uq_miaoshou_sync_cursors_credential_resource",
        ),
        schema="miaoshou",
    )

    op.create_table(
        "sync_issues",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("credential_id", sa.BigInteger, nullable=False),
        sa.Column("resource", sa.Text, nullable=False),
        sa.Column("issue_type", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text),
        sa.Column("details", postgresql.JSONB),
        sa.Column(
            "detected_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["integration.credentials.id"], ondelete="CASCADE"
        ),
        schema="miaoshou",
    )
    op.create_index(
        "ix_miaoshou_sync_issues_resource_resolved",
        "sync_issues",
        ["resource", "resolved_at"],
        schema="miaoshou",
    )

    for table_name in (
        "packages",
        "package_items",
        "package_gift_items",
        "sync_cursors",
        "sync_issues",
    ):
        op.execute(
            f"CREATE OR REPLACE TRIGGER trg_miaoshou_{table_name}_touch "
            f"BEFORE UPDATE ON miaoshou.{table_name} FOR EACH ROW "
            "EXECUTE FUNCTION public.fn_touch_updated_at()"
        )

    # Relocate rows written by the superseded package integration. Preserve
    # every capture under miaoshou before removing generic-schema rows.
    op.execute(
        """
        INSERT INTO miaoshou.package_raw_records (
            credential_id, external_package_id, endpoint, payload,
            payload_hash, captured_at, created_at
        )
        SELECT credential_id, external_id, endpoint, payload,
               payload_hash, captured_at, captured_at
        FROM integration.raw_records
        WHERE endpoint IN (
            'miaoshou.package.search_package_list',
            'miaoshou.package.get_package_info'
        )
        """
    )
    op.execute(
        """
        WITH captures AS (
            SELECT *
            FROM miaoshou.package_raw_records
            WHERE credential_id IS NOT NULL
              AND external_package_id IS NOT NULL
        ), merged AS (
            SELECT
                credential_id,
                external_package_id,
                (array_agg(id ORDER BY captured_at DESC, id DESC))[1] AS raw_record_id,
                (array_agg(endpoint ORDER BY captured_at DESC, id DESC))[1] AS source_endpoint,
                (array_agg(payload ORDER BY captured_at DESC, id DESC))[1] AS raw_payload,
                max(captured_at) AS captured_at,
                (array_agg(payload->>'platform' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload ? 'platform'))[1] AS platform,
                (array_agg(payload->>'site' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload ? 'site'))[1] AS site,
                (array_agg(payload->>'shopId' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload ? 'shopId'))[1] AS shop_id,
                (array_agg(payload->>'shopName' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload ? 'shopName'))[1] AS shop_name,
                (array_agg(payload->>'shopNick' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload ? 'shopNick'))[1] AS shop_nick,
                (array_agg(payload->>'appPackageNo' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload ? 'appPackageNo'))[1] AS app_package_no,
                (array_agg(payload->>'appPackageStatus' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload ? 'appPackageStatus'))[1] AS app_package_status,
                (array_agg(payload->>'appPackageStatusText' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload ? 'appPackageStatusText'))[1] AS app_package_status_text,
                (array_agg(payload->>'platformPackageStatus' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload ? 'platformPackageStatus'))[1] AS platform_package_status,
                (array_agg(payload->>'fulfillmentType' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload ? 'fulfillmentType'))[1] AS fulfillment_type,
                (array_agg(payload#>>'{orderInfo,platformOrderSn}' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload->'orderInfo' ? 'platformOrderSn'))[1] AS platform_order_sn,
                (array_agg(payload#>>'{orderInfo,platformOrderStatus}' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload->'orderInfo' ? 'platformOrderStatus'))[1] AS platform_order_status,
                (array_agg(payload#>>'{orderInfo,currency}' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload->'orderInfo' ? 'currency'))[1] AS currency,
                (array_agg(payload#>>'{orderInfo,gmtOrderStart}' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload->'orderInfo' ? 'gmtOrderStart'))[1] AS gmt_order_start,
                (array_agg(payload#>>'{orderInfo,gmtOrderModified}' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload->'orderInfo' ? 'gmtOrderModified'))[1] AS gmt_order_modified,
                (array_agg(payload#>>'{orderInfo,gmtDelivery}' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload->'orderInfo' ? 'gmtDelivery'))[1] AS gmt_delivery,
                (array_agg(COALESCE(NULLIF(payload->>'logisticsNo', ''),
                                    NULLIF(payload#>>'{logisticsAgentProductInfo,logisticsNo}', ''),
                                    NULLIF(payload#>>'{opOrderPackageToPlatformLastMile,logisticsNo}', ''))
                    ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload ? 'logisticsNo'
                         OR payload->'logisticsAgentProductInfo' ? 'logisticsNo'
                         OR payload->'opOrderPackageToPlatformLastMile' ? 'logisticsNo'))[1] AS logistics_no,
                (array_agg(COALESCE(NULLIF(payload#>>'{logisticsAgentProductInfo,logisticsCompany}', ''),
                                    NULLIF(payload#>>'{opOrderPackageToPlatformLastMile,logisticsCompany}', ''))
                    ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload->'logisticsAgentProductInfo' ? 'logisticsCompany'
                         OR payload->'opOrderPackageToPlatformLastMile' ? 'logisticsCompany'))[1] AS logistics_company,
                (array_agg(COALESCE(NULLIF(payload#>>'{logisticsAgentProductInfo,logisticsAgentProductId}', ''),
                                    NULLIF(payload#>>'{opOrderPackageToPlatformLastMile,logisticsAgentProductId}', ''))
                    ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload->'logisticsAgentProductInfo' ? 'logisticsAgentProductId'
                         OR payload->'opOrderPackageToPlatformLastMile' ? 'logisticsAgentProductId'))[1] AS logistics_product_id,
                (array_agg(COALESCE(NULLIF(payload#>>'{logisticsAgentProductInfo,productName}', ''),
                                    NULLIF(payload#>>'{opOrderPackageToPlatformLastMile,productName}', ''))
                    ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload->'logisticsAgentProductInfo' ? 'productName'
                         OR payload->'opOrderPackageToPlatformLastMile' ? 'productName'))[1] AS logistics_product_name,
                (array_agg(payload->'orderInfo' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload ? 'orderInfo'))[1] AS order_info,
                (array_agg(payload->'consigneeInfo' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload ? 'consigneeInfo'))[1] AS consignee_info,
                (array_agg(payload->'logisticsAgentProductInfo' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload ? 'logisticsAgentProductInfo'))[1] AS logistics_info,
                (array_agg(payload->'opOrderPackageToPlatformLastMile' ORDER BY captured_at DESC, id DESC)
                    FILTER (WHERE payload ? 'opOrderPackageToPlatformLastMile'))[1] AS last_mile_info
            FROM captures
            GROUP BY credential_id, external_package_id
        )
        INSERT INTO miaoshou.packages (
            credential_id, external_package_id, raw_record_id, source_endpoint,
            platform, site, shop_id, shop_name, shop_nick,
            app_package_no, app_package_status, app_package_status_text,
            platform_package_status, fulfillment_type,
            platform_order_sn, platform_order_status, currency,
            logistics_no, logistics_company, logistics_product_id,
            logistics_product_name, source_created_at, source_updated_at,
            shipped_at, order_info, consignee_info, logistics_info,
            last_mile_info, raw_payload, synced_at, created_at, updated_at
        )
        SELECT
            credential_id, external_package_id, raw_record_id, source_endpoint,
            platform, site, shop_id, shop_name, shop_nick,
            app_package_no, app_package_status, app_package_status_text,
            platform_package_status, fulfillment_type,
            platform_order_sn, platform_order_status, currency,
            logistics_no, logistics_company, logistics_product_id,
            logistics_product_name,
            CASE WHEN gmt_order_start ~ '^\\d{4}-\\d{2}-\\d{2}( \\d{2}:\\d{2}:\\d{2})?$'
                 THEN gmt_order_start::timestamp AT TIME ZONE 'Asia/Shanghai' END,
            CASE WHEN gmt_order_modified ~ '^\\d{4}-\\d{2}-\\d{2}( \\d{2}:\\d{2}:\\d{2})?$'
                 THEN gmt_order_modified::timestamp AT TIME ZONE 'Asia/Shanghai' END,
            CASE WHEN gmt_delivery ~ '^\\d{4}-\\d{2}-\\d{2}( \\d{2}:\\d{2}:\\d{2})?$'
                 THEN gmt_delivery::timestamp AT TIME ZONE 'Asia/Shanghai' END,
            order_info, consignee_info, logistics_info, last_mile_info,
            raw_payload, captured_at, captured_at, captured_at
        FROM merged
        ON CONFLICT (credential_id, external_package_id) DO NOTHING
        """
    )
    op.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (credential_id, external_package_id)
                credential_id, external_package_id, payload
            FROM miaoshou.package_raw_records
            WHERE external_package_id IS NOT NULL
              AND jsonb_typeof(payload->'items') = 'array'
            ORDER BY credential_id, external_package_id, captured_at DESC, id DESC
        )
        INSERT INTO miaoshou.package_items (
            package_id, external_package_item_id, external_order_item_id,
            platform_order_item_index, platform_product_id, platform_sku_id,
            platform_item_num, platform_outer_sku_id, title, sku_name,
            quantity, original_price, discounted_price, image_url,
            original_image_url, raw_payload
        )
        SELECT
            p.id,
            item->>'opOrderPackageItemId',
            item->>'opOrderItemId',
            item->>'platformOrderItemIndex',
            item->>'platformItemId',
            item->>'platformSkuId',
            item->>'platformItemNum',
            item->>'platformOuterSkuId',
            item->>'title',
            item->>'skuSubName',
            CASE WHEN item->>'quantity' ~ '^-?[0-9]+(\\.[0-9]+)?$'
                 THEN (item->>'quantity')::numeric END,
            CASE WHEN item->>'originalPrice' ~ '^-?[0-9]+(\\.[0-9]+)?$'
                 THEN (item->>'originalPrice')::numeric END,
            CASE WHEN item->>'discountedPrice' ~ '^-?[0-9]+(\\.[0-9]+)?$'
                 THEN (item->>'discountedPrice')::numeric END,
            item->>'picUrl',
            item->>'originalPicUrl',
            item
        FROM latest l
        JOIN miaoshou.packages p
          ON p.credential_id = l.credential_id
         AND p.external_package_id = l.external_package_id
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(l.payload->'items') = 'array'
                 THEN l.payload->'items' ELSE '[]'::jsonb END
        ) item
        WHERE NULLIF(item->>'opOrderPackageItemId', '') IS NOT NULL
        ON CONFLICT (package_id, external_package_item_id) DO NOTHING
        """
    )
    op.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (credential_id, external_package_id)
                credential_id, external_package_id, payload
            FROM miaoshou.package_raw_records
            WHERE external_package_id IS NOT NULL
              AND jsonb_typeof(payload->'giftItems') = 'array'
            ORDER BY credential_id, external_package_id, captured_at DESC, id DESC
        )
        INSERT INTO miaoshou.package_gift_items (
            package_id, external_gift_item_id, goods_id, goods_sku_id,
            goods_name, item_num, sku_name, goods_sku_outer_id, quantity,
            original_price, discounted_price, image_url, raw_payload
        )
        SELECT
            p.id,
            gift->>'opOrderPackageGiftId',
            gift->>'goodsId',
            gift->>'goodsSkuId',
            gift->>'goodsName',
            gift->>'itemNum',
            gift->>'goodsSkuSubName',
            gift->>'goodsSkuOuterId',
            CASE WHEN gift->>'quantity' ~ '^-?[0-9]+(\\.[0-9]+)?$'
                 THEN (gift->>'quantity')::numeric END,
            CASE WHEN gift->>'originalPrice' ~ '^-?[0-9]+(\\.[0-9]+)?$'
                 THEN (gift->>'originalPrice')::numeric END,
            CASE WHEN gift->>'discountedPrice' ~ '^-?[0-9]+(\\.[0-9]+)?$'
                 THEN (gift->>'discountedPrice')::numeric END,
            gift->>'picUrl',
            gift
        FROM latest l
        JOIN miaoshou.packages p
          ON p.credential_id = l.credential_id
         AND p.external_package_id = l.external_package_id
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(l.payload->'giftItems') = 'array'
                 THEN l.payload->'giftItems' ELSE '[]'::jsonb END
        ) gift
        WHERE NULLIF(gift->>'opOrderPackageGiftId', '') IS NOT NULL
        ON CONFLICT (package_id, external_gift_item_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO miaoshou.sync_cursors (
            credential_id, resource, cursor_value, cursor_epoch_ms,
            created_at, updated_at
        )
        SELECT c.id, 'packages', sc.cursor_value, sc.cursor_epoch_ms,
               sc.created_at, sc.updated_at
        FROM integration.sync_cursors sc
        JOIN integration.credentials c
          ON sc.scope = 'credential:' || c.id::text
        WHERE sc.job_name = 'miaoshou.packages'
        ON CONFLICT (credential_id, resource) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO miaoshou.sync_issues (
            credential_id, resource, issue_type, external_id, details,
            detected_at, resolved_at, created_at, updated_at
        )
        SELECT
            c.id, 'packages', si.issue_type, si.external_id, si.details,
            si.detected_at, si.resolved_at, si.created_at, si.updated_at
        FROM integration.sync_issues si
        JOIN integration.credentials c
          ON c.id = CASE
              WHEN si.details->>'credential_id' ~ '^[0-9]+$'
              THEN (si.details->>'credential_id')::bigint
              ELSE NULL
          END
        WHERE si.job_name IN ('miaoshou.packages', 'miaoshou.package_detail')
        """
    )

    # Remove only package-integration projections; unrelated fulfillment and
    # integration rows are untouched. shipment_lines cascade with shipment,
    # though the superseded job never populated them.
    op.execute(
        """
        DELETE FROM fulfillment.shipments s
        USING integration.raw_records r
        WHERE s.raw_record_id = r.id
          AND r.endpoint IN (
              'miaoshou.package.search_package_list',
              'miaoshou.package.get_package_info'
          )
        """
    )
    op.execute(
        "DELETE FROM integration.sync_cursors WHERE job_name = 'miaoshou.packages'"
    )
    op.execute(
        "DELETE FROM integration.sync_issues "
        "WHERE job_name IN ('miaoshou.packages', 'miaoshou.package_detail')"
    )
    op.execute(
        """
        DELETE FROM integration.raw_records old
        WHERE old.endpoint IN (
            'miaoshou.package.search_package_list',
            'miaoshou.package.get_package_info'
        )
          AND EXISTS (
              SELECT 1
              FROM miaoshou.package_raw_records copied
              WHERE copied.credential_id IS NOT DISTINCT FROM old.credential_id
                AND copied.external_package_id IS NOT DISTINCT FROM old.external_id
                AND copied.endpoint = old.endpoint
                AND copied.payload_hash = old.payload_hash
                AND copied.captured_at = old.captured_at
          )
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0045 relocates and removes source rows from generic schemas; "
        "restore from a pre-migration database backup instead of downgrading"
    )
