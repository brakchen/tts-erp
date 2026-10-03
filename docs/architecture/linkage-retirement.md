# Linkage schema retirement

> Decision date: 2026-09-30
> Migration: `0044_drop_linkage_schema`
> Production migration and service restart: human-operated

## Decision

Remove the unused `linkage` schema, its six tables, its view, and the
`/v2/linkage/*` HTTP surface. The design accumulated move-collect evidence but
never converted it into effective production links. At retirement time only
`link_evidence` contained rows; every effective-link, override, issue, account,
and variant table was empty.

The remaining business requirement uses the identity already stored in the
normalized models:

```text
commerce.products_spu.spu_id
= procurement.procurement_products.external_product_id
```

Reporting uses that direct identity for latest purchase-order cost and source
price. Manual cost remains the highest-priority source.

## Production archive

A custom-format archive of the complete schema was created and inspected with
`pg_restore --list` before migration authoring:

- path: `/home/schan/backups/tts_erp_manual/linkage_schema_20260930T042224Z.dump`
- SHA-256: `5e167d6cd41f0a7270140ce6b40db97a1a42ba2216a199163fc56f93fe3957d3`
- row counts: `link_evidence=716`; the other five tables were empty

The archive contains the schema, all six tables, table data, sequences,
constraints, indexes, triggers, and `effective_product_links` view.

## Deployment order

1. Merge and deploy code that no longer imports or queries linkage objects.
2. Stop `tts-erp.service` and `tts-erp-sync.service` so old processes cannot
   query the retired schema during migration.
3. Recheck archive checksum and current linkage row counts.
4. Human operator runs guarded `alembic upgrade head` with the documented
   production destructive opt-in.
5. Restart both services.
6. Run `prod-switch/postswitch-smoke.sh` and confirm `/v2/linkage/*` returns
   404 while reporting and move-collect jobs remain healthy.

Agents must not run the production migration or enable production override
variables.

## Rollback

Migration 0044 is intentionally irreversible through Alembic because empty DDL
cannot restore evidence data. To roll back:

1. stop API and sync-worker services;
2. restore the custom-format dump with `pg_restore`;
3. deploy the pre-0044 application;
4. run `alembic stamp 0043_focused_spus`;
5. restart and smoke-test.

## Removed surface

- tables: `account_links`, `product_links`, `variant_links`, `link_evidence`,
  `link_overrides`, `link_issues`
- view: `effective_product_links`
- API: every `/v2/linkage/*` endpoint
- runtime packages: `tts_erp_v2.linkage`, linkage ORM models, and router
- enum contracts: relation type, override decision, and linkage issue type
