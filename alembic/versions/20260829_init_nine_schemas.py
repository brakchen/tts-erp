"""init nine schemas + linkage.effective_product_links view

Revision ID: 0001_init_nine_schemas
Revises:
Create Date: 2026-08-29

Hand-written initial migration. We bypass alembic autogenerate for this
revision because:

  1. Existing public.* legacy tables are NOT in our target_metadata.
     Autogenerate would happily emit DROP TABLE for them.
  2. The `effective_product_links` VIEW is not a table — autogenerate
     cannot emit CREATE VIEW.
  3. Schema CREATE SCHEMA must come before table CREATE.

All 35 tts_erp_v2 tables are emitted here in dependency order. Downgrade
is the symmetric DROP. Subsequent revisions can use autogenerate freely
because by then the live DB has no legacy public.* tables to confuse it
(after we've done the §7.1 cutover).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0001_init_nine_schemas"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── CREATE SCHEMA (before any CREATE TABLE inside it) ─────────────
    for schema in (
        "integration",
        "commerce",
        "procurement",
        "fulfillment",
        "after_sales",
        "finance",
        "linkage",
        "reporting",
        "security",
    ):
        op.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

    # ── integration.* (5) ──────────────────────────────────────────────
    op.execute("""CREATE TABLE integration.credentials (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        provider TEXT NOT NULL,
        external_account_id TEXT NOT NULL,
        account_label TEXT,
        ciphertext BYTEA NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE,
        granted_scopes JSONB,
        company_secret_ciphertext BYTEA,
        extra JSONB,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_credentials_provider_account UNIQUE (provider, external_account_id)
    )""")
    op.execute("CREATE INDEX ix_credentials_provider ON integration.credentials (provider)")

    op.execute("""CREATE TABLE integration.raw_records (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        credential_id BIGINT,
        endpoint TEXT NOT NULL,
        external_id TEXT,
        captured_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        payload JSONB NOT NULL,
        payload_hash VARCHAR(64),
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(credential_id) REFERENCES integration.credentials(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_raw_records_endpoint_account ON integration.raw_records (endpoint, credential_id)")
    op.execute("CREATE INDEX ix_raw_records_external_id ON integration.raw_records (external_id)")
    op.execute("CREATE INDEX ix_raw_records_captured_at ON integration.raw_records (captured_at)")

    op.execute("""CREATE TABLE integration.sync_jobs (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        job_name TEXT NOT NULL,
        credential_id BIGINT,
        started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        finished_at TIMESTAMP WITH TIME ZONE,
        status TEXT DEFAULT 'running' NOT NULL,
        rows_total INTEGER DEFAULT 0 NOT NULL,
        rows_inserted INTEGER DEFAULT 0 NOT NULL,
        rows_updated INTEGER DEFAULT 0 NOT NULL,
        rows_failed INTEGER DEFAULT 0 NOT NULL,
        error_message TEXT,
        extra JSONB,
        PRIMARY KEY (id),
        FOREIGN KEY(credential_id) REFERENCES integration.credentials(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_sync_jobs_name_started ON integration.sync_jobs (job_name, started_at)")

    op.execute("""CREATE TABLE integration.sync_cursors (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        job_name TEXT NOT NULL,
        scope TEXT NOT NULL,
        cursor_value TEXT,
        cursor_epoch_ms BIGINT,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_sync_cursors_job_scope UNIQUE (job_name, scope)
    )""")

    op.execute("""CREATE TABLE integration.sync_issues (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        job_name TEXT NOT NULL,
        issue_type TEXT NOT NULL,
        external_id TEXT,
        details JSONB,
        detected_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        resolved_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id)
    )""")
    op.execute("CREATE INDEX ix_sync_issues_job_resolved ON integration.sync_issues (job_name, resolved_at)")

    # ── commerce.* (5) ────────────────────────────────────────────────
    op.execute("""CREATE TABLE commerce.shops (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        platform TEXT NOT NULL,
        external_account_id TEXT NOT NULL,
        account_name TEXT,
        region TEXT,
        seller_type TEXT,
        status TEXT,
        credential_id BIGINT,
        source_updated_at TIMESTAMP WITH TIME ZONE,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_shops_platform_ext UNIQUE (platform, external_account_id),
        FOREIGN KEY(credential_id) REFERENCES integration.credentials(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_shops_status ON commerce.shops (status)")

    op.execute("""CREATE TABLE commerce.products_spu (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        shop_pk BIGINT NOT NULL,
        external_product_id TEXT NOT NULL,
        title TEXT,
        category_id TEXT,
        status TEXT,
        main_image_url TEXT,
        source_created_at TIMESTAMP WITH TIME ZONE,
        source_updated_at TIMESTAMP WITH TIME ZONE,
        raw_record_id BIGINT,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_products_spu_account_ext UNIQUE (shop_pk, external_product_id),
        FOREIGN KEY(shop_pk) REFERENCES commerce.shops(id) ON DELETE RESTRICT,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_products_spu_status ON commerce.products_spu (status)")

    op.execute("""CREATE TABLE commerce.products_sku (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        spu_pk BIGINT NOT NULL,
        external_variant_id TEXT NOT NULL,
        seller_sku TEXT,
        variant_name TEXT,
        attributes JSONB,
        image_url TEXT,
        status TEXT,
        source_updated_at TIMESTAMP WITH TIME ZONE,
        raw_record_id BIGINT,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_channel_variants_product_ext UNIQUE (spu_pk, external_variant_id),
        FOREIGN KEY(spu_pk) REFERENCES commerce.products_spu(id) ON DELETE RESTRICT,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_channel_variants_seller_sku ON commerce.products_sku (seller_sku)")

    op.execute("""CREATE TABLE commerce.sales_orders (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        shop_pk BIGINT NOT NULL,
        order_id TEXT NOT NULL,
        status TEXT,
        currency TEXT,
        payment_amount NUMERIC(20, 4),
        total_amount NUMERIC(20, 4),
        fulfillment_type TEXT,
        source_created_at TIMESTAMP WITH TIME ZONE,
        source_updated_at TIMESTAMP WITH TIME ZONE,
        paid_at TIMESTAMP WITH TIME ZONE,
        shipped_at TIMESTAMP WITH TIME ZONE,
        delivered_at TIMESTAMP WITH TIME ZONE,
        cancelled_at TIMESTAMP WITH TIME ZONE,
        raw_record_id BIGINT,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_sales_orders_account_ext UNIQUE (shop_pk, order_id),
        FOREIGN KEY(shop_pk) REFERENCES commerce.shops(id) ON DELETE RESTRICT,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_sales_orders_status ON commerce.sales_orders (status)")
    op.execute("CREATE INDEX ix_sales_orders_paid_at ON commerce.sales_orders (paid_at)")

    op.execute("""CREATE TABLE commerce.sales_order_lines (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        order_pk BIGINT NOT NULL,
        external_line_id TEXT NOT NULL,
        spu_pk BIGINT,
        sku_pk BIGINT,
        external_product_id_snapshot TEXT,
        external_variant_id_snapshot TEXT,
        product_name_snapshot TEXT,
        variant_name_snapshot TEXT,
        image_url_snapshot TEXT,
        quantity NUMERIC(20, 4),
        unit_price NUMERIC(20, 4),
        currency TEXT,
        line_status TEXT,
        raw_record_id BIGINT,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_sales_order_lines_order_ext UNIQUE (order_pk, external_line_id),
        FOREIGN KEY(order_pk) REFERENCES commerce.sales_orders(id) ON DELETE RESTRICT,
        FOREIGN KEY(spu_pk) REFERENCES commerce.products_spu(id) ON DELETE SET NULL,
        FOREIGN KEY(sku_pk) REFERENCES commerce.products_sku(id) ON DELETE SET NULL,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_sales_order_lines_channel_product ON commerce.sales_order_lines (spu_pk)")
    op.execute("CREATE INDEX ix_sales_order_lines_channel_variant ON commerce.sales_order_lines (sku_pk)")

    # ── procurement.* (6) ─────────────────────────────────────────────
    op.execute("""CREATE TABLE procurement.procurement_accounts (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        provider TEXT NOT NULL,
        external_account_id TEXT NOT NULL,
        account_name TEXT,
        status TEXT,
        credential_id BIGINT,
        source_updated_at TIMESTAMP WITH TIME ZONE,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_procurement_accounts_provider_ext UNIQUE (provider, external_account_id),
        FOREIGN KEY(credential_id) REFERENCES integration.credentials(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_procurement_accounts_status ON procurement.procurement_accounts (status)")

    op.execute("""CREATE TABLE procurement.procurement_products (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        procurement_account_id BIGINT NOT NULL,
        external_product_id TEXT NOT NULL,
        product_type TEXT,
        title TEXT,
        source_platform TEXT,
        source_item_id TEXT,
        source_item_url TEXT,
        status TEXT,
        raw_record_id BIGINT,
        source_updated_at TIMESTAMP WITH TIME ZONE,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_procurement_products_account_ext UNIQUE (procurement_account_id, external_product_id),
        FOREIGN KEY(procurement_account_id) REFERENCES procurement.procurement_accounts(id) ON DELETE RESTRICT,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_procurement_products_status ON procurement.procurement_products (status)")
    op.execute("CREATE INDEX ix_procurement_products_product_type ON procurement.procurement_products (product_type)")

    op.execute("""CREATE TABLE procurement.procurement_product_variants (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        procurement_product_id BIGINT NOT NULL,
        external_variant_id TEXT NOT NULL,
        variant_name TEXT,
        attributes JSONB,
        supplier_sku TEXT,
        status TEXT,
        raw_record_id BIGINT,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_procurement_variants_product_ext UNIQUE (procurement_product_id, external_variant_id),
        FOREIGN KEY(procurement_product_id) REFERENCES procurement.procurement_products(id) ON DELETE RESTRICT,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_procurement_variants_supplier_sku ON procurement.procurement_product_variants (supplier_sku)")

    op.execute("""CREATE TABLE procurement.purchase_orders (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        procurement_account_id BIGINT NOT NULL,
        external_purchase_order_id TEXT NOT NULL,
        supplier_id TEXT,
        status TEXT,
        currency TEXT,
        total_amount NUMERIC(20, 4),
        source_created_at TIMESTAMP WITH TIME ZONE,
        source_updated_at TIMESTAMP WITH TIME ZONE,
        paid_at TIMESTAMP WITH TIME ZONE,
        completed_at TIMESTAMP WITH TIME ZONE,
        raw_record_id BIGINT,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_purchase_orders_account_ext UNIQUE (procurement_account_id, external_purchase_order_id),
        FOREIGN KEY(procurement_account_id) REFERENCES procurement.procurement_accounts(id) ON DELETE RESTRICT,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_purchase_orders_status ON procurement.purchase_orders (status)")

    op.execute("""CREATE TABLE procurement.purchase_order_lines (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        purchase_order_id BIGINT NOT NULL,
        external_line_id TEXT NOT NULL,
        procurement_product_id BIGINT NOT NULL,
        procurement_product_variant_id BIGINT,
        quantity NUMERIC(20, 4),
        unit_cost NUMERIC(20, 4),
        currency TEXT,
        line_status TEXT,
        raw_record_id BIGINT,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_purchase_order_lines_order_ext UNIQUE (purchase_order_id, external_line_id),
        FOREIGN KEY(purchase_order_id) REFERENCES procurement.purchase_orders(id) ON DELETE RESTRICT,
        FOREIGN KEY(procurement_product_id) REFERENCES procurement.procurement_products(id) ON DELETE RESTRICT,
        FOREIGN KEY(procurement_product_variant_id) REFERENCES procurement.procurement_product_variants(id) ON DELETE SET NULL,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_purchase_order_lines_product ON procurement.purchase_order_lines (procurement_product_id)")

    op.execute("""CREATE TABLE procurement.manual_product_costs (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        spu_pk BIGINT NOT NULL,
        unit_cost NUMERIC(20, 4) NOT NULL,
        currency TEXT NOT NULL,
        valid_from TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        valid_to TIMESTAMP WITH TIME ZONE,
        note TEXT,
        created_by TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(spu_pk) REFERENCES commerce.products_spu(id) ON DELETE RESTRICT
    )""")
    op.execute("CREATE INDEX ix_manual_costs_channel_product_valid ON procurement.manual_product_costs (spu_pk, valid_from)")

    # ── fulfillment.* (3) ─────────────────────────────────────────────
    op.execute("""CREATE TABLE fulfillment.shipments (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        order_pk BIGINT NOT NULL,
        external_package_id TEXT NOT NULL,
        tracking_number TEXT,
        provider_id TEXT,
        provider_name TEXT,
        status TEXT,
        shipped_at TIMESTAMP WITH TIME ZONE,
        delivered_at TIMESTAMP WITH TIME ZONE,
        raw_record_id BIGINT,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_shipments_order_ext UNIQUE (order_pk, external_package_id),
        FOREIGN KEY(order_pk) REFERENCES commerce.sales_orders(id) ON DELETE RESTRICT,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_shipments_tracking_number ON fulfillment.shipments (tracking_number)")
    op.execute("CREATE INDEX ix_shipments_status ON fulfillment.shipments (status)")

    op.execute("""CREATE TABLE fulfillment.shipment_lines (
        shipment_id BIGINT NOT NULL,
        sales_order_line_id BIGINT NOT NULL,
        quantity NUMERIC(20, 4),
        PRIMARY KEY (shipment_id, sales_order_line_id),
        FOREIGN KEY(shipment_id) REFERENCES fulfillment.shipments(id) ON DELETE CASCADE,
        FOREIGN KEY(sales_order_line_id) REFERENCES commerce.sales_order_lines(id) ON DELETE RESTRICT
    )""")

    op.execute("""CREATE TABLE fulfillment.tracking_events (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        shipment_id BIGINT NOT NULL,
        external_event_key TEXT NOT NULL,
        action_code INTEGER,
        event_at TIMESTAMP WITH TIME ZONE,
        description TEXT,
        location TEXT,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_tracking_events_shipment_key UNIQUE (shipment_id, external_event_key),
        FOREIGN KEY(shipment_id) REFERENCES fulfillment.shipments(id) ON DELETE CASCADE
    )""")
    op.execute("CREATE INDEX ix_tracking_events_event_at ON fulfillment.tracking_events (event_at)")

    # ── after_sales.* (2) ─────────────────────────────────────────────
    op.execute("""CREATE TABLE after_sales.cases (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        shop_pk BIGINT NOT NULL,
        order_pk BIGINT NOT NULL,
        external_case_id TEXT NOT NULL,
        case_type TEXT NOT NULL,
        status TEXT,
        reason_code TEXT,
        reason_text TEXT,
        created_at_source TIMESTAMP WITH TIME ZONE,
        updated_at_source TIMESTAMP WITH TIME ZONE,
        raw_record_id BIGINT,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_cases_account_ext UNIQUE (shop_pk, external_case_id),
        FOREIGN KEY(shop_pk) REFERENCES commerce.shops(id) ON DELETE RESTRICT,
        FOREIGN KEY(order_pk) REFERENCES commerce.sales_orders(id) ON DELETE RESTRICT,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_cases_sales_order ON after_sales.cases (order_pk)")
    op.execute("CREATE INDEX ix_cases_case_type_status ON after_sales.cases (case_type, status)")

    op.execute("""CREATE TABLE after_sales.case_lines (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        case_id BIGINT NOT NULL,
        sales_order_line_id BIGINT NOT NULL,
        external_case_line_id TEXT,
        quantity NUMERIC(20, 4),
        refund_amount NUMERIC(20, 4),
        currency TEXT,
        should_replenish_stock BOOLEAN,
        PRIMARY KEY (id),
        CONSTRAINT uq_case_lines_case_ext UNIQUE (case_id, external_case_line_id),
        FOREIGN KEY(case_id) REFERENCES after_sales.cases(id) ON DELETE CASCADE,
        FOREIGN KEY(sales_order_line_id) REFERENCES commerce.sales_order_lines(id) ON DELETE RESTRICT
    )""")
    op.execute("CREATE INDEX ix_case_lines_sales_order_line ON after_sales.case_lines (sales_order_line_id)")

    # ── finance.* (4) ─────────────────────────────────────────────────
    op.execute("""CREATE TABLE finance.payouts (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        shop_pk BIGINT NOT NULL,
        external_payout_id TEXT NOT NULL,
        status TEXT,
        currency TEXT,
        amount NUMERIC(20, 4),
        source_created_at TIMESTAMP WITH TIME ZONE,
        source_updated_at TIMESTAMP WITH TIME ZONE,
        raw_record_id BIGINT,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_payouts_account_ext UNIQUE (shop_pk, external_payout_id),
        FOREIGN KEY(shop_pk) REFERENCES commerce.shops(id) ON DELETE RESTRICT,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_payouts_status ON finance.payouts (status)")

    op.execute("""CREATE TABLE finance.settlement_statements (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        payout_id BIGINT NOT NULL,
        external_statement_id TEXT NOT NULL,
        statement_time TIMESTAMP WITH TIME ZONE,
        period_start DATE,
        period_end DATE,
        currency TEXT,
        raw_record_id BIGINT,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_settlement_statements_payout_ext UNIQUE (payout_id, external_statement_id),
        FOREIGN KEY(payout_id) REFERENCES finance.payouts(id) ON DELETE RESTRICT,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_settlement_statements_statement_time ON finance.settlement_statements (statement_time)")

    op.execute("""CREATE TABLE finance.settlement_transactions (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        settlement_statement_id BIGINT NOT NULL,
        external_transaction_id TEXT NOT NULL,
        order_pk BIGINT,
        sales_order_line_id BIGINT,
        after_sales_case_id BIGINT,
        transaction_time TIMESTAMP WITH TIME ZONE,
        raw_record_id BIGINT,
        synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_settlement_txn_stmt_ext UNIQUE (settlement_statement_id, external_transaction_id),
        FOREIGN KEY(settlement_statement_id) REFERENCES finance.settlement_statements(id) ON DELETE RESTRICT,
        FOREIGN KEY(order_pk) REFERENCES commerce.sales_orders(id) ON DELETE SET NULL,
        FOREIGN KEY(sales_order_line_id) REFERENCES commerce.sales_order_lines(id) ON DELETE SET NULL,
        FOREIGN KEY(after_sales_case_id) REFERENCES after_sales.cases(id) ON DELETE SET NULL,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_settlement_txn_sales_order ON finance.settlement_transactions (order_pk)")
    op.execute("CREATE INDEX ix_settlement_txn_order_line ON finance.settlement_transactions (sales_order_line_id)")
    op.execute("CREATE INDEX ix_settlement_txn_case ON finance.settlement_transactions (after_sales_case_id)")

    op.execute("""CREATE TABLE finance.settlement_components (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        transaction_id BIGINT NOT NULL,
        component_code TEXT NOT NULL,
        amount NUMERIC(20, 4) NOT NULL,
        currency TEXT NOT NULL,
        source_order INTEGER,
        PRIMARY KEY (id),
        CONSTRAINT uq_settlement_components_txn_code UNIQUE (transaction_id, component_code),
        FOREIGN KEY(transaction_id) REFERENCES finance.settlement_transactions(id) ON DELETE CASCADE
    )""")
    op.execute("CREATE INDEX ix_settlement_components_code ON finance.settlement_components (component_code)")

    # ── linkage.* (6 tables + 1 VIEW) ─────────────────────────────────
    op.execute("""CREATE TABLE linkage.account_links (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        procurement_account_id BIGINT NOT NULL,
        shop_pk BIGINT NOT NULL,
        external_relation_id TEXT,
        status TEXT,
        valid_from TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        valid_to TIMESTAMP WITH TIME ZONE,
        source_updated_at TIMESTAMP WITH TIME ZONE,
        raw_record_id BIGINT,
        PRIMARY KEY (id),
        CONSTRAINT uq_account_links_triplet UNIQUE (procurement_account_id, shop_pk, external_relation_id),
        FOREIGN KEY(procurement_account_id) REFERENCES procurement.procurement_accounts(id) ON DELETE RESTRICT,
        FOREIGN KEY(shop_pk) REFERENCES commerce.shops(id) ON DELETE RESTRICT,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_account_links_validity ON linkage.account_links (valid_from, valid_to)")

    op.execute("""CREATE TABLE linkage.product_links (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        procurement_product_id BIGINT NOT NULL,
        spu_pk BIGINT NOT NULL,
        external_relation_id TEXT,
        relation_type TEXT NOT NULL,
        status TEXT,
        is_primary BOOLEAN,
        valid_from TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        valid_to TIMESTAMP WITH TIME ZONE,
        source_updated_at TIMESTAMP WITH TIME ZONE,
        raw_record_id BIGINT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_product_links_pivot_validfrom UNIQUE (procurement_product_id, spu_pk, valid_from),
        FOREIGN KEY(procurement_product_id) REFERENCES procurement.procurement_products(id) ON DELETE RESTRICT,
        FOREIGN KEY(spu_pk) REFERENCES commerce.products_spu(id) ON DELETE RESTRICT,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_product_links_status ON linkage.product_links (status)")
    op.execute("CREATE INDEX ix_product_links_channel_product ON linkage.product_links (spu_pk)")

    op.execute("""CREATE TABLE linkage.variant_links (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        procurement_product_variant_id BIGINT NOT NULL,
        sku_pk BIGINT NOT NULL,
        external_relation_id TEXT,
        status TEXT,
        valid_from TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        valid_to TIMESTAMP WITH TIME ZONE,
        raw_record_id BIGINT,
        PRIMARY KEY (id),
        CONSTRAINT uq_variant_links_pivot_validfrom UNIQUE (procurement_product_variant_id, sku_pk, valid_from),
        FOREIGN KEY(procurement_product_variant_id) REFERENCES procurement.procurement_product_variants(id) ON DELETE RESTRICT,
        FOREIGN KEY(sku_pk) REFERENCES commerce.products_sku(id) ON DELETE RESTRICT,
        FOREIGN KEY(raw_record_id) REFERENCES integration.raw_records(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_variant_links_validity ON linkage.variant_links (valid_from, valid_to)")

    op.execute("""CREATE TABLE linkage.link_evidence (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        product_link_id BIGINT,
        variant_link_id BIGINT,
        evidence_type TEXT NOT NULL,
        source_table TEXT,
        source_external_id TEXT,
        evidence_payload JSONB,
        observed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        FOREIGN KEY(product_link_id) REFERENCES linkage.product_links(id) ON DELETE SET NULL,
        FOREIGN KEY(variant_link_id) REFERENCES linkage.variant_links(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_link_evidence_product_link ON linkage.link_evidence (product_link_id)")
    op.execute("CREATE INDEX ix_link_evidence_variant_link ON linkage.link_evidence (variant_link_id)")

    op.execute("""CREATE TABLE linkage.link_overrides (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        procurement_product_id BIGINT NOT NULL,
        spu_pk BIGINT NOT NULL,
        decision TEXT NOT NULL,
        reason TEXT,
        valid_from TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        valid_to TIMESTAMP WITH TIME ZONE,
        created_by TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_link_overrides_pivot_validfrom UNIQUE (procurement_product_id, spu_pk, valid_from),
        FOREIGN KEY(procurement_product_id) REFERENCES procurement.procurement_products(id) ON DELETE RESTRICT,
        FOREIGN KEY(spu_pk) REFERENCES commerce.products_spu(id) ON DELETE RESTRICT
    )""")
    op.execute("CREATE INDEX ix_link_overrides_decision ON linkage.link_overrides (decision)")

    op.execute("""CREATE TABLE linkage.link_issues (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        issue_type TEXT NOT NULL,
        procurement_product_id BIGINT,
        spu_pk BIGINT,
        candidate_count INTEGER,
        status TEXT,
        details JSONB,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        resolved_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id),
        FOREIGN KEY(procurement_product_id) REFERENCES procurement.procurement_products(id) ON DELETE SET NULL,
        FOREIGN KEY(spu_pk) REFERENCES commerce.products_spu(id) ON DELETE SET NULL
    )""")
    op.execute("CREATE INDEX ix_link_issues_type_resolved ON linkage.link_issues (issue_type, resolved_at)")

    # VIEW: effective_product_links (override priority → valid miaoshou link)
    op.execute("""CREATE OR REPLACE VIEW linkage.effective_product_links AS
        SELECT
            cp.id   AS spu_pk,
            COALESCE(lo.procurement_product_id, pl.procurement_product_id) AS procurement_product_id,
            COALESCE(lo.decision, pl.relation_type) AS effective_relation_type,
            COALESCE(lo.id, pl.id) AS source_link_id,
            CASE WHEN lo.id IS NOT NULL THEN 'OPERATOR_OVERRIDE' ELSE 'MIAOSHOU_PUBLISHED_TO_TIKTOK' END AS source_kind,
            COALESCE(lo.valid_from, pl.valid_from) AS effective_from,
            pp.procurement_account_id,
            cp.shop_pk
        FROM commerce.products_spu cp
        LEFT JOIN linkage.link_overrides lo
               ON lo.spu_pk = cp.id
              AND lo.valid_to IS NULL
        LEFT JOIN linkage.product_links pl
               ON pl.spu_pk = cp.id
              AND pl.valid_to IS NULL
              AND (lo.id IS NULL OR lo.decision <> 'DENY')
        LEFT JOIN procurement.procurement_products pp
               ON pp.id = COALESCE(lo.procurement_product_id, pl.procurement_product_id)
        WHERE COALESCE(lo.decision, 'ALLOW') <> 'DENY'
    """)

    # ── reporting.* (3) ───────────────────────────────────────────────
    op.execute("""CREATE TABLE reporting.product_cost_snapshots (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        spu_pk BIGINT NOT NULL,
        cost_method TEXT NOT NULL,
        unit_cost NUMERIC(20, 4) NOT NULL,
        currency TEXT NOT NULL,
        valid_from TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        valid_to TIMESTAMP WITH TIME ZONE,
        source_purchase_quantity NUMERIC(20, 4),
        source_purchase_amount NUMERIC(20, 4),
        source_line_count INTEGER,
        calculation_version INTEGER DEFAULT 1 NOT NULL,
        calculated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_cost_snapshots_pivot_version UNIQUE (spu_pk, valid_from, calculation_version),
        FOREIGN KEY(spu_pk) REFERENCES commerce.products_spu(id) ON DELETE RESTRICT
    )""")
    op.execute("CREATE INDEX ix_cost_snapshots_method ON reporting.product_cost_snapshots (cost_method)")

    op.execute("""CREATE TABLE reporting.product_profit_daily (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        spu_pk BIGINT NOT NULL,
        profit_date DATE NOT NULL,
        units_sold NUMERIC(20, 4),
        gross_revenue NUMERIC(20, 4),
        estimated_cogs NUMERIC(20, 4),
        platform_fees NUMERIC(20, 4),
        shipping_cost NUMERIC(20, 4),
        refunds NUMERIC(20, 4),
        estimated_gross_profit NUMERIC(20, 4),
        currency TEXT,
        cost_method TEXT,
        calculation_version INTEGER DEFAULT 1 NOT NULL,
        calculated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_profit_daily_pivot_version UNIQUE (spu_pk, profit_date, calculation_version),
        FOREIGN KEY(spu_pk) REFERENCES commerce.products_spu(id) ON DELETE RESTRICT
    )""")
    op.execute("CREATE INDEX ix_profit_daily_profit_date ON reporting.product_profit_daily (profit_date)")

    op.execute("""CREATE TABLE reporting.shipment_tracking_summary (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        shipment_id BIGINT NOT NULL,
        tracking_number TEXT,
        first_event_at TIMESTAMP WITH TIME ZONE,
        last_event_at TIMESTAMP WITH TIME ZONE,
        last_event_description TEXT,
        last_location TEXT,
        event_count INTEGER,
        calculation_version INTEGER DEFAULT 1 NOT NULL,
        calculated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_tracking_summary_shipment_version UNIQUE (shipment_id, calculation_version),
        FOREIGN KEY(shipment_id) REFERENCES fulfillment.shipments(id) ON DELETE CASCADE
    )""")

    # ── security.* (1) ────────────────────────────────────────────────
    op.execute("""CREATE TABLE security.api_keys (
        id BIGINT GENERATED ALWAYS AS IDENTITY NOT NULL,
        key_hash TEXT NOT NULL,
        key_prefix TEXT NOT NULL,
        name TEXT,
        role TEXT NOT NULL,
        status TEXT DEFAULT 'active' NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        last_used_at TIMESTAMP WITH TIME ZONE,
        rotated_to_key_hash TEXT,
        PRIMARY KEY (id),
        CONSTRAINT ix_api_keys_key_hash UNIQUE (key_hash)
    )""")
    op.execute("CREATE INDEX ix_api_keys_role ON security.api_keys (role)")


def downgrade() -> None:
    # Reverse in dependency order. We do not drop public.* — that's
    # the legacy mirror, never our concern.
    op.execute("DROP VIEW IF EXISTS linkage.effective_product_links")
    for schema, tables in (
        ("security", ["api_keys"]),
        ("reporting", ["shipment_tracking_summary", "product_profit_daily", "product_cost_snapshots"]),
        ("linkage", ["link_issues", "link_overrides", "link_evidence", "variant_links", "product_links", "account_links"]),
        ("finance", ["settlement_components", "settlement_transactions", "settlement_statements", "payouts"]),
        ("after_sales", ["case_lines", "cases"]),
        ("fulfillment", ["tracking_events", "shipment_lines", "shipments"]),
        ("procurement", ["manual_product_costs", "purchase_order_lines", "purchase_orders", "procurement_product_variants", "procurement_products", "procurement_accounts"]),
        ("commerce", ["sales_order_lines", "sales_orders", "products_sku", "products_spu", "shops"]),
        ("integration", ["sync_issues", "sync_cursors", "sync_jobs", "raw_records", "credentials"]),
    ):
        for tbl in tables:
            op.execute(f'DROP TABLE IF EXISTS {schema}.{tbl} CASCADE')
        op.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
