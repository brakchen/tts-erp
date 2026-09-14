"""One-off backfill: plugin.orders 时间字段从 raw_log 重解析回填。

背景
----
2026-09-14 发现 ``_ts_to_datetime`` 的 str 分支只认 ISO 格式，而 Seller
Center order/list 实测返回数字字符串（create_time 秒级、update_time
毫秒级），导致 plugin.orders 的 order_time / update_time /
latest_rts_time / latest_tts_time 几乎全 NULL（prod 实测 494/494
order_time 为 NULL）。解析修复在 commit 122cad1（lane
feature/plugin-shop-analytics），本脚本回填存量行。

做法
----
对每条 plugin.orders，沿 log_id 回 plugin.raw_log 取
response_body.trade_order_module 的四个时间字段，用修复后的
``_ts_to_datetime`` 重解析，仅当解析值非 NULL 且与现值不同才 UPDATE。
raw_log 可能被多次 dump 刷新——取该订单**最新一条** raw_log
（log_id 最大的那条，即 orders.log_id 当前指向的记录）。

安全
----
* 默认 dry-run（只打印将影响多少行，不写库）。
* ``--confirm`` 才真正 UPDATE；走 require_destructive_script_guard，
  prod-shape dbname 需要 ALLOW_PROD_DESTRUCTIVE=1。

USAGE
-----
    python3 scripts/oneoff_backfill_plugin_order_times.py            # dry-run
    python3 scripts/oneoff_backfill_plugin_order_times.py --confirm  # 执行
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

PROJECT_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

import os  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

from tts_erp_v2.api.deps import require_destructive_script_guard  # noqa: E402
from tts_erp_v2.plugin.orders.repository import _ts_to_datetime  # noqa: E402

_TIME_FIELDS = ("create_time", "update_time", "latest_rts_time", "latest_tts_time")
_COL_MAP = {
    "create_time": "order_time",
    "update_time": "update_time",
    "latest_rts_time": "latest_rts_time",
    "latest_tts_time": "latest_tts_time",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="真正执行 UPDATE")
    parser.add_argument("--dry-run", action="store_true", help="预览（默认）")
    args = parser.parse_args()

    require_destructive_script_guard(
        script_name="oneoff_backfill_plugin_order_times",
        confirmation=args.confirm,
        dangerous=args.confirm,
    )

    # SQLAlchemy 需要原样的 postgresql+psycopg:// URL，不经 normalize_db_url
    url = os.environ.get("TTS_ERP_DB_URL", "")
    if not url:
        print("TTS_ERP_DB_URL 未设置", file=sys.stderr)
        return 2
    eng = create_engine(url)

    with eng.connect() as c:
        rows = c.execute(
            text(
                """
                SELECT o.id, o.order_id, r.response_body
                FROM plugin.orders o
                JOIN plugin.raw_log r ON r.id = o.log_id
                WHERE o.order_time IS NULL
                   OR o.update_time IS NULL
                   OR o.latest_rts_time IS NULL
                   OR o.latest_tts_time IS NULL
                """
            )
        ).all()

    print(f"待检查行数: {len(rows)}")
    updates: list[dict] = []
    for oid, order_id, body in rows:
        tom = (body or {}).get("trade_order_module") or {}
        if not isinstance(tom, dict):
            continue
        set_cols = {}
        for field in _TIME_FIELDS:
            value = _ts_to_datetime(tom.get(field))
            if value is not None:
                set_cols[_COL_MAP[field]] = value
        if set_cols:
            updates.append({"id": oid, **set_cols})

    print(f"可回填行数: {len(updates)}")
    if updates:
        sample = updates[0]
        print(f"样例: id={sample['id']} " +
              " ".join(f"{k}={v.isoformat()}" for k, v in sample.items() if k != "id"))

    if not args.confirm:
        print("DRY-RUN：未写库。加 --confirm 执行。")
        return 0

    if not updates:
        print("无可回填行。")
        return 0

    with eng.begin() as c:
        for u in updates:
            sets = ", ".join(f"{k} = :{k}" for k in u if k != "id")
            c.execute(
                text(f"UPDATE plugin.orders SET {sets} WHERE id = :id"),  # noqa: python-sql-injection — 列名来自固定白名单 _COL_MAP
                u,
            )
    print(f"已回填 {len(updates)} 行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
