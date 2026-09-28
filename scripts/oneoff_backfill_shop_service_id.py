"""One-off backfill: 把环境变量 TIKTOK_SERVICE_ID 写入所有 service_id 为 NULL 的店铺。

背景
----
lane feature/service-id-per-shop 在 commerce.shops 表新增了 service_id 字段，
用于存储每个店铺对应的 TikTok Partner Center service_id（开发者授权用）。

已有店铺在迁移后 service_id 为 NULL。本脚本读取环境变量 TIKTOK_SERVICE_ID，
批量更新到这些老店铺。幂等：WHERE 限定 service_id IS NULL，已填充的行不动。

USAGE
-----
    # 1. dry-run preview（不写库；打印将更新的行数）
    python3 scripts/oneoff_backfill_shop_service_id.py --dry-run

    # 2. 实跑（prod-shape db 需要 ALLOW_PROD_DESTRUCTIVE=1）
    ALLOW_PROD_DESTRUCTIVE=1 python3 scripts/oneoff_backfill_shop_service_id.py --confirm

验证（实跑后）
------------
    -- 所有 tiktok 店铺都应有 service_id
    SELECT shop_id, service_id FROM commerce.shops
    WHERE platform = 'tiktok' ORDER BY shop_id;

    -- 残留 NULL 数（应为 0）
    SELECT count(*) FROM commerce.shops
    WHERE platform = 'tiktok' AND service_id IS NULL;
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent

sys.path.insert(0, str(PROJECT_ROOT))


def _load_env() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def _resolve_db_url() -> str:
    raw = os.environ.get("TTS_ERP_DB_URL", "").strip()
    if not raw:
        sys.exit(
            "TTS_ERP_DB_URL is not set; export it (or have it in .env) "
            "before running this script."
        )
    scheme_end = raw.find("://")
    if "+" in raw[:scheme_end]:
        return "postgresql://" + raw[scheme_end + 3 :]
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill service_id on commerce.shops from the "
        "TIKTOK_SERVICE_ID environment variable. "
        "Run --dry-run first; --confirm to actually write."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print stats; do not touch the DB.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="REQUIRED to actually execute the UPDATE.",
    )
    args = parser.parse_args(argv)
    if not args.dry_run and not args.confirm:
        parser.error("pass either --dry-run (preview) or --confirm (execute)")

    # ── Resolve service_id from env ──
    service_id = os.environ.get("TIKTOK_SERVICE_ID", "").strip()
    if not service_id:
        sys.exit(
            "TIKTOK_SERVICE_ID is not set in the environment or .env file. "
            "Nothing to backfill."
        )
    print(f"[config] TIKTOK_SERVICE_ID = {service_id!r}")

    # ── Guard: prod-shape db requires explicit opt-in (AGENTS.md §6) ──
    from tts_erp_v2.api.deps import require_destructive_script_guard

    require_destructive_script_guard(
        script_name="oneoff_backfill_shop_service_id",
        confirmation=args.confirm,
        dangerous=args.confirm,
    )

    import psycopg

    conn = psycopg.connect(_resolve_db_url())
    try:
        # ── Pre-check ──
        with conn.cursor() as cur:
            row = cur.fetchone()
            assert row is not None
            to_update = row[0]

            cur.execute(
                "SELECT count(*) FROM commerce.shops "
                "WHERE platform = 'tiktok' AND service_id IS NOT NULL"
            )
            row = cur.fetchone()
            assert row is not None
            already_set = row[0]

        print(
            f"[precheck] tiktok shops: {to_update} with service_id=NULL, "
            f"{already_set} already set"
        )

        if to_update == 0:
            print("[precheck] nothing to update; exiting.")
            return 0

        if args.dry_run:
            # Show which rows would be touched
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT shop_id, account_name FROM commerce.shops "
                    "WHERE platform = 'tiktok' AND service_id IS NULL "
                    "ORDER BY shop_id"
                )
                rows = cur.fetchall()
            print(f"[dry-run] would update {len(rows)} rows:")
            for shop_id, name in rows:
                print(f"  shop_id={shop_id}  account_name={name!r}  → service_id={service_id!r}")
            conn.rollback()
            return 0

        # ── Execute ──
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE commerce.shops SET service_id = %s, updated_at = now() "
                "WHERE platform = 'tiktok' AND service_id IS NULL",
                (service_id,),
            )
            updated = cur.rowcount
        conn.commit()
        print(f"[done] updated {updated} rows; committed")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
