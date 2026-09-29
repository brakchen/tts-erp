"""One-off backfill: 从 URL query string 提取 seller_id 填到历史 intercepted_requests。

背景
----
Manifest V3 扩展 ``webRequest.onBeforeSendHeaders`` 默认拿不到 cookie / authorization，
所以 chrome 扩展从 2026-09-13 第一次 burst 起，所有 intercepted_requests 行的
``seller_id`` / ``advertiser_id`` 列都是 NULL。lane ``feat/settlement-data-usability``
(e1ca6ba) 在 ``/v2/intercept/sync`` 端点加了 ``_extract_seller_id_from_url(url)``，
**未来** 新写入的请求会被回填（优先 ``oec_seller_id`` → ``seller_id``）。

但**存量 3638+ 条历史记录**仍为 NULL，filter ``?seller_id=`` 查不到。
本脚本只读 ``url`` 列 + 调同样的 helper 函数 + UPDATE 一次，幂等（WHERE 限定
``seller_id IS NULL``，已填充的行不动）。Non-destructive：只填 NULL → 字符串，
不覆盖任何非空值。

为什么不在 SQL 里做？
--------------------
postgresql 的 ``url_parse`` 函数扩展不一定装，且 Python 端 helper 与 sync 端点
是 single source of truth（lane e1ca6ba 之后改 helper 时 backfill 自动跟随），
避免双份实现漂移。

USAGE
-----
    # 1. dry-run preview（不写库；打印将填充 / 无法填充的统计）
    python3 scripts/oneoff_backfill_intercept_seller_id.py --dry-run

    # 2. 实跑（prod-shape db 需要 ALLOW_PROD_DESTRUCTIVE=1）
    ALLOW_PROD_DESTRUCTIVE=1 python3 scripts/oneoff_backfill_intercept_seller_id.py --confirm

验证（实跑后）
------------
    -- 残留为 0：seller_id 仍为 NULL 且 url 不含 oec_seller_id/seller_id 的记录
    SELECT count(*) FROM plugin.intercepted_requests
    WHERE seller_id IS NULL
      AND url NOT LIKE '%oec_seller_id=%'
      AND url NOT LIKE '%seller_id=%';

    -- seller_id 填充前后对比（应填回约 96 条 / 3638，2026-09-13 13:21 burst 抽样）
    SELECT count(*) FILTER (WHERE seller_id IS NOT NULL) AS filled,
           count(*) FILTER (WHERE seller_id IS NULL) AS still_null
    FROM plugin.intercepted_requests;
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent

# Make ``tts_erp_v2`` importable from any CWD.
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


_SQL_SELECT_CANDIDATES = (
    "SELECT id, url FROM plugin.intercepted_requests "
    "WHERE seller_id IS NULL "
    "  AND (url LIKE '%oec_seller_id=%' OR url LIKE '%seller_id=%') "
    "ORDER BY id"
)

_SQL_UPDATE_BATCH = (
    "UPDATE plugin.intercepted_requests "
    "SET seller_id = %s "
    "WHERE id = %s AND seller_id IS NULL"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill seller_id on historical intercepted_requests "
        "by extracting oec_seller_id / seller_id from the URL query string. "
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
        help="REQUIRED to actually execute the UPDATE. Safety belt so a "
        "stray invocation does not modify the intercept table.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="UPDATE batch size (default 500). Smaller = more progress "
        "logging, larger = fewer round-trips.",
    )
    args = parser.parse_args(argv)
    if not args.dry_run and not args.confirm:
        parser.error("pass either --dry-run (preview) or --confirm (execute)")

    # ── Guard: prod-shape db requires explicit opt-in (AGENTS.md §6) ──
    # Imported lazily so the script's --help / --dry-run / DB-free preflight
    # don't require psycopg or tts_erp_v2 deps to be importable.
    from tts_erp_v2.api.deps import require_destructive_script_guard
    from tts_erp_v2.api.v2.intercept import _extract_seller_id_from_url

    require_destructive_script_guard(
        script_name="oneoff_backfill_intercept_seller_id",
        confirmation=args.confirm,
        dangerous=args.confirm,  # --confirm = write, --dry-run = preview
    )

    import psycopg

    conn = psycopg.connect(_resolve_db_url())
    try:
        # ── Pre-check: total NULL rows + how many have a candidate URL ──
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM plugin.intercepted_requests "
                "WHERE seller_id IS NULL"
            )
            total_null = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM plugin.intercepted_requests "
                "WHERE seller_id IS NULL "
                "  AND (url LIKE '%oec_seller_id=%' OR url LIKE '%seller_id=%')"
            )
            with_candidate_url = cur.fetchone()[0]
        print(
            f"[precheck] seller_id IS NULL: {total_null}; "
            f"with oec_seller_id / seller_id in url: {with_candidate_url}"
        )
        if with_candidate_url == 0:
            print("[precheck] nothing to fill; exiting.")
            return 0

        # ── Stream candidates and extract seller_id in Python ──
        with conn.cursor() as cur:
            cur.execute(_SQL_SELECT_CANDIDATES)
            rows = cur.fetchall()
        print(f"[scan] read {len(rows)} candidate rows")

        # Build (seller_id, row_id) tuples, dropping any the helper returns None for.
        updates: list[tuple[str, int]] = []
        skipped_no_extract = 0
        for row_id, url in rows:
            extracted = _extract_seller_id_from_url(url)
            if extracted:
                updates.append((extracted, row_id))
            else:
                # URL has the key but parser produced nothing (malformed);
                # shouldn't happen in practice but log so we notice.
                skipped_no_extract += 1
        print(
            f"[extract] {len(updates)} rows have a parseable seller_id; "
            f"{skipped_no_extract} skipped (helper returned None)"
        )

        if args.dry_run:
            # Show a tiny sample so the operator can sanity-check.
            print("[dry-run sample] first 3 updates:")
            for sid, rid in updates[:3]:
                print(f"  id={rid}  seller_id={sid!r}")
            print(
                f"[dry-run] would UPDATE {len(updates)} rows in "
                f"{(len(updates) + args.batch_size - 1) // args.batch_size} batches"
            )
            conn.rollback()
            return 0

        # ── Execute: batched UPDATE in a single transaction ──
        total_updated = 0
        for i in range(0, len(updates), args.batch_size):
            batch = updates[i : i + args.batch_size]
            with conn.cursor() as cur:
                cur.executemany(_SQL_UPDATE_BATCH, batch)
                batch_updated = cur.rowcount
            total_updated += batch_updated
            print(
                f"[update] batch {i // args.batch_size + 1}: "
                f"{batch_updated}/{len(batch)} rows updated"
            )
        conn.commit()
        print(f"[done] updated {total_updated} rows; committed")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
