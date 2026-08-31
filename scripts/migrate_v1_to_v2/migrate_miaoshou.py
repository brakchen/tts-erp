"""Migrate miaoshou_* tables → procurement.* + linkage.link_evidence.

Source tables (read-only):
  * public.miaoshou_shops              (1 row; license/authorization)
  * public.miaoshou_move_collect_tasks (237 rows; collect+publish evidence)
  * public.miaoshou_collect_box_details (0 rows — empty, skipped)
  * public.miaoshou_price_templates    (0 rows — empty, skipped)

Target tables (v2, writeable):
  * procurement.procurement_accounts     (UNIQUE provider, external_account_id)
  * procurement.procurement_products     (UNIQUE procurement_account_id,
                                            external_product_id)
  * linkage.link_evidence                (one row per move_collect task)

Mapping:
  * miaoshou_shops → procurement_accounts
      external_account_id = str(shop_id) (e.g. "17060852")
      provider = "miaoshou"
      account_name = platform_shop_name
  * miaoshou_move_collect_tasks → procurement_products + link_evidence
      * external_product_id = platform_item_id (TikTok SPU; per survey the
        platform_item_id IS the SPU, not a SKU). When NULL (failed tasks
        with no published product), external_product_id falls back to
        ``source_item_id`` (the 1688/17zwd item id) so the row still
        lands in procurement_products.
      * product_type = "COLLECTED_PRODUCT" (the survey decision tree)
      * source_platform = source ('1688' / '17zwd' / …)
      * source_item_id = source_item_id
      * source_item_url = source_item_url
      * title, source_updated_at = gmt_modified → UTC timestamptz
      * evidence: one linkage.link_evidence row per task:
            evidence_type = "MOVE_COLLECT_TASK"
            source_table = "public.miaoshou_move_collect_tasks"
            source_external_id = move_collect_task_detail_id
            evidence_payload = JSON snapshot (status / reason / breadcrumb
                / etc.)

Time conversion:
  * gmt_* strings are UTC+8 (CN wall-clock, no tz). → UTC timestamptz via
    common.gmt8_string_to_utc().

Implementation notes:
  * SQL is plain string + psycopg ``%(name)s`` pyformat placeholders, passed
    via ``conn.exec_driver_sql()``.

Idempotency:
  * procurement_accounts/procurement_products use ON CONFLICT DO UPDATE.
  * link_evidence has no UNIQUE constraint in the v2 schema. To make the
    migration re-runnable, we DELETE prior ``migrate.miaoshou`` evidence
    rows at the start of each run (scoped to evidence_type / source_table
    filter) and re-insert. This is safe because evidence is a derived view
    over source tasks; if a task is gone from source we want its evidence
    gone too.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy.engine import Engine

from scripts.migrate_v1_to_v2.common import (
    DryRunSink,
    get_source_engine,
    get_target_engine,
    gmt8_string_to_utc,
    require_prod_guard,
)


@dataclass
class MigrationStats:
    shops_seen: int = 0
    shops_upserted: int = 0
    accounts_upserted: int = 0
    products_seen: int = 0
    products_upserted: int = 0
    products_unresolved: int = 0
    evidence_written: int = 0
    evidence_skipped: int = 0

    def report(self, dry_run: bool) -> str:
        mode = "DRY-RUN" if dry_run else "APPLIED"
        return (
            f"{mode} miaoshou migration:\n"
            f"  source public.miaoshou_shops              seen={self.shops_seen}\n"
            f"  source public.miaoshou_move_collect_tasks seen={self.products_seen}\n"
            f"  procurement.procurement_accounts          upserted={self.accounts_upserted}\n"
            f"  procurement.procurement_products          upserted={self.products_upserted}\n"
            f"  linkage.link_evidence                     written={self.evidence_written}\n"
        )


# ─── source readers ──────────────────────────────────────────────────


_SHOPS_SQL = (
    "SELECT shop_id, platform, site, platform_shop_name, shop_nick, "
    "       parent_shop_id, is_cb, is_cnsc, status, "
    "       gmt_expire, gmt_last_auth "
    "FROM public.miaoshou_shops"
)


_TASKS_SQL = (
    "SELECT platform, move_collect_task_detail_id, collect_box_detail_id, "
    "       shop_id, item_num, cid, source, source_site, source_item_id, "
    "       title, thumbnail, is_timing, status, reason, "
    "       gmt_create, gmt_modified, platform_item_id, is_renew_item, "
    "       shop_name, site_name, site, source_item_url, item_edit_url, "
    "       breadcrumb, owner_sub_app_account_id, owner_sub_account_alias_name "
    "FROM public.miaoshou_move_collect_tasks"
)


def _iter_shops(source: Engine) -> Iterator[dict]:
    with source.connect() as conn:
        for row in conn.exec_driver_sql(_SHOPS_SQL).mappings():
            yield dict(row)


def _iter_tasks(source: Engine) -> Iterator[dict]:
    with source.connect() as conn:
        for row in conn.exec_driver_sql(_TASKS_SQL).mappings():
            yield dict(row)


# ─── upserts ──────────────────────────────────────────────────────────


_UPSERT_ACCOUNT = (
    "INSERT INTO procurement.procurement_accounts "
    "    (provider, external_account_id, account_name, status, "
    "     source_updated_at, synced_at) "
    "VALUES "
    "    (%(provider)s, %(external_account_id)s, %(account_name)s, "
    "     %(status)s, %(source_updated_at)s, now()) "
    "ON CONFLICT (provider, external_account_id) DO UPDATE SET "
    "    account_name      = EXCLUDED.account_name, "
    "    status            = EXCLUDED.status, "
    "    source_updated_at = EXCLUDED.source_updated_at, "
    "    synced_at         = now() "
    "RETURNING id"
)


_UPSERT_PRODUCT = (
    "INSERT INTO procurement.procurement_products "
    "    (procurement_account_id, external_product_id, product_type, "
    "     title, source_platform, source_item_id, source_item_url, "
    "     status, source_updated_at, synced_at) "
    "VALUES "
    "    (%(procurement_account_id)s, %(external_product_id)s, "
    "     %(product_type)s, %(title)s, "
    "     %(source_platform)s, %(source_item_id)s, %(source_item_url)s, "
    "     %(status)s, %(source_updated_at)s, now()) "
    "ON CONFLICT (procurement_account_id, external_product_id) DO UPDATE SET "
    "    product_type       = EXCLUDED.product_type, "
    "    title              = EXCLUDED.title, "
    "    source_platform    = EXCLUDED.source_platform, "
    "    source_item_id     = EXCLUDED.source_item_id, "
    "    source_item_url    = EXCLUDED.source_item_url, "
    "    status             = EXCLUDED.status, "
    "    source_updated_at  = EXCLUDED.source_updated_at, "
    "    synced_at          = now() "
    "RETURNING id"
)


_INSERT_EVIDENCE = (
    "INSERT INTO linkage.link_evidence "
    "    (product_link_id, variant_link_id, evidence_type, "
    "     source_table, source_external_id, evidence_payload, observed_at) "
    "VALUES "
    "    (%(product_link_id)s, %(variant_link_id)s, %(evidence_type)s, "
    "     %(source_table)s, %(source_external_id)s, "
    "     CAST(%(evidence_payload)s AS jsonb), now()) "
    "RETURNING id"
)


_CLEAR_PRIOR_EVIDENCE = (
    "DELETE FROM linkage.link_evidence "
    "WHERE evidence_type = %(evidence_type)s "
    "  AND source_table  = %(source_table)s"
)


# ─── main pipeline ──────────────────────────────────────────────────


def run(dry_run: bool = False, batch_size: int = 500,
        verbose: bool = True) -> MigrationStats:
    # 2026-08-30 incident guard: refuse to write to prod unless the
    # kill-switch is set. dry_run=True skips the check.
    require_prod_guard(dry_run, action="migrate_miaoshou.run()")
    _ = batch_size
    stats = MigrationStats()
    sink = DryRunSink()
    source = get_source_engine()
    target = get_target_engine()

    # Idempotency for evidence: scope-delete prior migrate.miaoshou rows.
    if not dry_run:
        with target.connect() as conn, conn.begin():
            conn.exec_driver_sql(
                _CLEAR_PRIOR_EVIDENCE,
                {
                    "evidence_type": "MOVE_COLLECT_TASK",
                    "source_table": "public.miaoshou_move_collect_tasks",
                },
            )

    # ── Pass 1: shops → procurement_accounts ──────────────────────
    account_id_cache: dict[str, int] = {}

    with target.connect() as conn, conn.begin():
        for s in _iter_shops(source):
            stats.shops_seen += 1
            external_account_id = str(s["shop_id"])
            params = {
                "provider": "miaoshou",
                "external_account_id": external_account_id,
                "account_name": s.get("platform_shop_name"),
                "status": s.get("status"),
                "source_updated_at": gmt8_string_to_utc(s.get("gmt_last_auth")),
            }
            if dry_run:
                stats.accounts_upserted += 1
                # Use a unique object sentinel so ``is`` identity check works.
                account_id_cache[external_account_id] = id(params)
                sink.record("procurement.procurement_accounts", 1)
                continue
            row = conn.exec_driver_sql(_UPSERT_ACCOUNT, params).first()
            if row:
                stats.accounts_upserted += 1
                account_id_cache[external_account_id] = int(row[0])
                sink.record("procurement.procurement_accounts", 1)

    # ── Pass 2: move_collect_tasks → procurement_products + evidence ─
    with target.connect() as conn, conn.begin():
        for t in _iter_tasks(source):
            stats.products_seen += 1
            account_ext = str(t.get("shop_id") or "")
            acct_id = account_id_cache.get(account_ext)
            if acct_id is None:
                # No matching procurement_account — task has an unknown
                # shop_id (foreign-key missing).
                stats.products_unresolved += 1
                sink.record("procurement.procurement_products(SKIPPED)", 1)
                continue
            # Use platform_item_id (TikTok SPU) as external_product_id when
            # present; fall back to source_item_id for failed tasks where
            # we still want to record the source-side reference.
            platform_pid = (t.get("platform_item_id") or "").strip()
            source_pid = (t.get("source_item_id") or "").strip()
            external_product_id = platform_pid or source_pid
            if not external_product_id:
                stats.products_unresolved += 1
                sink.record("procurement.procurement_products(SKIPPED)", 1)
                continue

            params = {
                "procurement_account_id": acct_id,
                "external_product_id": external_product_id,
                "product_type": "COLLECTED_PRODUCT",
                "title": t.get("title"),
                "source_platform": t.get("source"),
                "source_item_id": source_pid or None,
                "source_item_url": t.get("source_item_url"),
                "status": t.get("status"),
                "source_updated_at": gmt8_string_to_utc(
                    t.get("gmt_modified")),
            }
            if dry_run:
                stats.products_upserted += 1
                sink.record("procurement.procurement_products", 1)
                # Dry-run: report evidence count too.
                stats.evidence_written += 1
                sink.record("linkage.link_evidence", 1)
                continue
            row = conn.exec_driver_sql(_UPSERT_PRODUCT, params).first()
            if not row:
                continue
            stats.products_upserted += 1
            sink.record("procurement.procurement_products", 1)

            # Insert one evidence row per move_collect task. We don't link
            # to a product_link here because the source → destination
            # mapping for ``miaoshou_published_to_tiktok`` requires a
            # TikTok shop + product (from the channel side) which isn't
            # guaranteed at migration time. Lane D's link-compute job
            # reads link_evidence later and creates product_links then.
            evidence_payload: dict[str, Any] = {
                "platform": t.get("platform"),
                "task_status": t.get("status"),
                "reason": t.get("reason"),
                "platform_item_id": t.get("platform_item_id"),
                "source_item_id": t.get("source_item_id"),
                "source_item_url": t.get("source_item_url"),
                "item_edit_url": t.get("item_edit_url"),
                "breadcrumb": t.get("breadcrumb"),
                "cid": t.get("cid"),
                "shop_name": t.get("shop_name"),
                "site": t.get("site"),
                "site_name": t.get("site_name"),
                "is_renew_item": t.get("is_renew_item"),
                "owner_sub_app_account_id": t.get("owner_sub_app_account_id"),
                "owner_sub_account_alias_name": t.get(
                    "owner_sub_account_alias_name"),
                "collect_box_detail_id": t.get("collect_box_detail_id"),
                "is_timing": t.get("is_timing"),
                "gmt_create": t.get("gmt_create"),
                "gmt_modified": t.get("gmt_modified"),
            }
            conn.exec_driver_sql(
                _INSERT_EVIDENCE,
                {
                    "product_link_id": None,
                    "variant_link_id": None,
                    "evidence_type": "MOVE_COLLECT_TASK",
                    "source_table": "public.miaoshou_move_collect_tasks",
                    "source_external_id": t.get(
                        "move_collect_task_detail_id"),
                    "evidence_payload": json.dumps(evidence_payload),
                },
            )
            stats.evidence_written += 1
            sink.record("linkage.link_evidence", 1)

    if verbose:
        print(stats.report(dry_run=dry_run))
        if dry_run:
            print(sink.report())
    return stats


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=("Migrate miaoshou_* tables → procurement.* + "
                     "linkage.link_evidence."),
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Report the migration plan without writing.")
    p.add_argument("--batch-size", type=int, default=500,
                   help="Rows per upsert batch (default 500).")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress the final summary print.")
    return p.parse_args(argv)


if __name__ == "__main__":  # pragma: no cover
    args = _parse_args()
    run(dry_run=args.dry_run, batch_size=args.batch_size,
        verbose=not args.quiet)
    sys.exit(0)
