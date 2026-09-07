"""One-off backfill: settlement_components 零值行补齐（D2，2026-09-07）。

背景
----
2026-09-07 拍板（tech-doc/analytics/spu-roi-v7-refactor.md §3.5，D2）：
上游 202309 statement_transactions payload 的 53 个 ``*_amount`` 字段全部
**显式传输**（0 = ``"0"`` 字符串，实测确认），显式零与字段缺失语义不同——
「SETTLEMENT=0」= 已结算但到手 0（全额退款/取消冲正单），必须落库；
「无 SETTLEMENT 行」从此唯一含义 = 未结算。

job 的 ``_write_components`` 已改为零值落库（本仓库同 commit），但存量交易
的组件行是旧规则写的（零跳过）。本脚本从 ``integration.raw_records`` 的
原始 payload 重放，**只补缺**（ON CONFLICT DO NOTHING——已有非零行一个字节
不动）。幂等，可重跑。

与 oneoff_regen_finance_components.py（2026-09-06，已执行完毕）的关系：
那次的 step 2 重建了非零组件；本脚本补它跳过的零值行。

USAGE
-----
    # 1. dry-run preview（不写库；打印将补齐的行数）
    python3 scripts/oneoff_backfill_settlement_zero_components.py --dry-run

    # 2. 实跑
    python3 scripts/oneoff_backfill_settlement_zero_components.py --confirm

验证（实跑后）
------------
    -- 应为 0：有交易但无 SETTLEMENT 组件行的订单
    SELECT count(*) FROM finance.settlement_transactions t
    WHERE NOT EXISTS (SELECT 1 FROM finance.settlement_components sc
                      WHERE sc.transaction_id = t.id
                        AND sc.component_code = 'SETTLEMENT');
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


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


def _run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="preview only")
    parser.add_argument("--confirm", action="store_true", help="execute backfill")
    args = parser.parse_args()
    if not args.dry_run and not args.confirm:
        parser.error("pass either --dry-run (preview) or --confirm (execute)")

    try:
        import psycopg
    except ImportError:  # pragma: no cover — operator env guard
        sys.exit("psycopg3 not importable; activate the repo venv (.venv)")

    conn = psycopg.connect(_resolve_db_url())

    # SQL is inlined at every execute call site (no helper indirection):
    # psycopg 3.3 types ``execute(query)`` as LiteralString-only (PEP 675)
    # and the repo security lint flags any non-literal sink.

    # ── Step 0: 覆盖预检 ─────────────────────────────────────────────
    row = conn.execute(
        "SELECT count(*), "
        "  count(*) FILTER (WHERE EXISTS ("
        "    SELECT 1 FROM integration.raw_records r "
        "    WHERE r.endpoint LIKE '%statement_transactions%' "
        "      AND r.external_id = t.external_transaction_id)) "
        "FROM finance.settlement_transactions t"
    ).fetchone()
    total_txn, txn_with_raw = (row[0], row[1]) if row else (0, 0)
    print(f"[0] settlement_transactions: {total_txn}, with raw payload: {txn_with_raw}")
    if txn_with_raw < total_txn:
        print(
            f"    ⚠ {total_txn - txn_with_raw} 笔交易无 raw payload（无法回填，"
            "这些交易将仍无零值行——需人工确认来源）"
        )

    # ── Step 1: 待补齐零值行计数（dry-run 的核心数字）────────────────
    row = conn.execute(
        "SELECT count(*) FROM ("
        "  SELECT t.id AS txn_id, upper(regexp_replace(k.key, '_amount$', '')) AS code "
        "  FROM finance.settlement_transactions t "
        "  JOIN ("
        "    SELECT DISTINCT ON (external_id) external_id, payload "
        "    FROM integration.raw_records "
        "    WHERE endpoint LIKE '%statement_transactions%' "
        "    ORDER BY external_id, captured_at DESC"
        "  ) rr ON rr.external_id = t.external_transaction_id "
        "  CROSS JOIN LATERAL jsonb_each(rr.payload) k "
        "  WHERE k.key LIKE '%\\_amount' "
        "    AND (k.value #>> '{}') IS NOT NULL "
        ") missing_candidates "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM finance.settlement_components sc "
        "  WHERE sc.transaction_id = missing_candidates.txn_id "
        "    AND sc.component_code = missing_candidates.code)"
    ).fetchone()
    missing_n = row[0] if row else 0
    print(f"[1] 缺失组件行（应为全部零值行）待补齐: {missing_n}")

    # ── Step 2: 实写（只补缺，已有行不动）─────────────────────────────
    if args.confirm and missing_n:
        cur = conn.execute(
            "INSERT INTO finance.settlement_components "
            "(transaction_id, component_code, amount, currency) "
            "SELECT t.id, "
            "       upper(regexp_replace(k.key, '_amount$', '')), "
            "       (k.value #>> '{}')::numeric, "
            "       COALESCE(rr.payload ->> 'currency', 'VND') "
            "FROM finance.settlement_transactions t "
            "JOIN ("
            "  SELECT DISTINCT ON (external_id) external_id, payload "
            "  FROM integration.raw_records "
            "  WHERE endpoint LIKE '%statement_transactions%' "
            "  ORDER BY external_id, captured_at DESC"
            ") rr ON rr.external_id = t.external_transaction_id "
            "CROSS JOIN LATERAL jsonb_each(rr.payload) k "
            "WHERE k.key LIKE '%\\_amount' "
            "  AND (k.value #>> '{}') IS NOT NULL "
            "ON CONFLICT (transaction_id, component_code) DO NOTHING"
        )
        print(f"[2] inserted {cur.rowcount} zero-value component rows")

    conn.commit()
    conn.close()
    print("dry-run complete — no writes" if args.dry_run else "backfill complete")


if __name__ == "__main__":
    _run()
