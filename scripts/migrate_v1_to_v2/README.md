# v1 → v2 Migration Scripts

This directory contains one-shot data migration scripts that copy the legacy
`public.*` mirror tables (and the separate `oauth_receiver.oauth_tokens`
table) into the new nine-schema target layout built by Lane 0.

## Run order

The scripts are deliberately ordered so each step's writes are visible to
the next:

```bash
python -m scripts.migrate_v1_to_v2.migrate_shops        # shops + oauth_tokens → channel_accounts + credentials
python -m scripts.migrate_v1_to_v2.migrate_orders       # orders + order_items → sales_orders + sales_order_lines
python -m scripts.migrate_v1_to_v2.migrate_logistics    # order_shippings + logistics_* → shipments + tracking_events
python -m scripts.migrate_v1_to_v2.migrate_after_sales  # returns + cancellations → cases + case_lines
python -m scripts.migrate_v1_to_v2.migrate_finance      # payments + statements + statement_transactions → payouts + settlement_*
python -m scripts.migrate_v1_to_v2.migrate_miaoshou     # miaoshou_* → procurement.* + linkage.link_evidence
python -m scripts.migrate_v1_to_v2.reconcile            # three-axis diff report
```

Each script accepts `--dry-run` so you can preview the plan before applying:

```bash
python -m scripts.migrate_v1_to_v2.migrate_orders --dry-run
```

Every script is **idempotent** (uses `ON CONFLICT DO UPDATE` against the
v2 unique constraints) and emits a counters block at the end of every
run showing source rows seen, target rows upserted, and exclusions.

## Three-axis reconciliation report

`reconcile.py` runs after the migrations and produces three diff axes:

1. **Row counts** between source `public.*` and target v2 schemas (with
   `MOCK_SHOP_12345` and other known exclusions subtracted on the source
   side).
2. **Amount sums** for `payment_amount`, `payouts.amount`,
   `settlement_components.amount`, etc.
3. **Coverage rates** for foreign-key associations
   (`channel_product_id` resolved on order lines,
   `sales_order_line_id` resolved on case lines,
   `payout_id` resolved on settlement statements).

The script exits non-zero if any check fails — but rows that diverge for a
*documented* reason (e.g. orphan settlement statements with NULL
`payment_id`) are surfaced in the `Exclusions / known divergences` block
and do not count as failures.

`--json` prints the report as machine-readable JSON for CI consumption.

## Rollback / fallback

The migration only writes to the v2 schemas. The legacy `public.*` tables
are never touched. To "undo" the migration:

1. Truncate the nine new schemas in a single transaction:

    ```sql
    TRUNCATE
        integration.credentials, integration.raw_records,
        integration.sync_jobs, integration.sync_cursors, integration.sync_issues,
        commerce.channel_accounts, commerce.channel_products,
        commerce.channel_product_variants, commerce.sales_orders,
        commerce.sales_order_lines,
        procurement.procurement_accounts, procurement.procurement_products,
        procurement.procurement_product_variants, procurement.purchase_orders,
        procurement.purchase_order_lines, procurement.manual_product_costs,
        fulfillment.shipments, fulfillment.shipment_lines,
        fulfillment.tracking_events,
        after_sales.cases, after_sales.case_lines,
        finance.payouts, finance.settlement_statements,
        finance.settlement_transactions, finance.settlement_components,
        linkage.account_links, linkage.product_links, linkage.variant_links,
        linkage.link_evidence, linkage.link_overrides, linkage.link_issues
        RESTART IDENTITY CASCADE;
    ```

2. Re-run `migrate_shops.py --dry-run` to confirm the source counts
   match the (now-empty) target counts.

The legacy 25-table layout in `public.*` is intact throughout.

## Explicit exclusions (always subtracted from source)

| Source | Count | Reason |
| --- | --- | --- |
| `public.shops` row `MOCK_SHOP_12345` | 1 | Synthetic test shop; `oauth_receiver.oauth_tokens` has the matching row, both are filtered. |
| `public.statements` rows with `payment_id IS NULL` | 16 | No parent payout to attach to; the FK constraint would reject them. Skipped in `migrate_finance`. |
| Their 33 child rows in `public.statement_transactions` | 33 | Cascaded skip from the previous row. |

The reconciliation report surfaces them as "Exclusions" so a reader can
audit the decision.

## Operational notes

* All scripts honor `--batch-size N` (default 500), but the implementation
  is a streaming row-by-row iterator — the value is currently used for
  accounting only.
* Source timestamps are interpreted as:
  * `epoch_seconds` (orders, returns, cancellations, statements, payments)
  * `epoch_milliseconds` (logistics events only)
  * `YYYY-MM-DD HH:MM:SS` Asia/Shanghai wall-clock (miaoshou `gmt_*`)
  All three convert to `TIMESTAMP WITH TIME ZONE` (UTC) on write.
* MOCK_SHOP_12345 is the only synthetic shop id observed in production;
  the filter is centralized in `common.is_real_shop_id()`.
* The `reconcile.py` script reads both source `public.*` and the new v2
  schemas; it never modifies either side.
