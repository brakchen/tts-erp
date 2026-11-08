#!/usr/bin/env python3
"""check_jobs_actually_run.py — 只读巡检：让「作业现状」可查询。

背景（2026-11-08 TTS-ERP 架构评审）：
  评审中反复出现「注释/文档说没跑、代码说在跑、库里又是另一回事」的三方矛盾。
  根因是**现状断言没有来自数据库**。本脚本把现状变成可查询的清单。

四张清单：
  ① 注册了但近 N 天没有任何行          —— 调度器注册了，作业从没被派发
  ② 有行但从无终态（永远 running）      —— 进程被 SIGKILL / OOM，僵尸行
  ③ 有 succeeded 但 rows_inserted 恒为 0 且 rows_total > 0
                                       —— 跑了、读了源数据、但一条没写
  ④ 源数据陈旧                          —— 作业 succeeded，但源表长期未变
                                          （"无陈旧数据可写"的另一种形态）

**本脚本只读**：只执行 SELECT，不写任何表，不改任何状态。

用法：
    python scripts/check_jobs_actually_run.py [--days 7] [--stale-days 7] [--json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from typing import Any

REPO_HINT = "从仓库根目录运行，或确保 tts_erp_v2 可 import"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _load_jobs() -> dict[str, Any]:
    try:
        from tts_erp_v2.sync_worker.scheduler import JOBS
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"cannot import JOBS: {exc}\n{REPO_HINT}\n")
        raise SystemExit(2)
    return dict(JOBS)


def _session_factory():
    from tts_erp_v2.db.base import get_session_factory

    return get_session_factory()


def _job_rows(session, days: int) -> list[Any]:
    from sqlalchemy import select

    from tts_erp_v2.db.models import SyncJob

    since = _utcnow() - dt.timedelta(days=days)
    stmt = (
        select(SyncJob)
        .where(SyncJob.started_at >= since)
        .order_by(SyncJob.started_at.desc())
    )
    return list(session.execute(stmt).scalars().all())


def _all_job_rows(session) -> list[Any]:
    from sqlalchemy import select

    from tts_erp_v2.db.models import SyncJob

    return list(
        session.execute(select(SyncJob).order_by(SyncJob.started_at.desc())).scalars().all()
    )


def _stale_tables(session, stale_days: int) -> list[dict[str, Any]]:
    """④ 列出「有 updated_at 列且很久没变过」的业务表。

    这是 ④ 的通用形态：不去猜每个作业的源表是谁，而是把**所有带
    updated_at 的表**的最近变更时间列出来，让陈旧显形。
    """
    from sqlalchemy import text

    rows = session.execute(
        text(
            """
            SELECT table_schema, table_name
              FROM information_schema.columns
             WHERE table_name LIKE '%%'
               AND column_name = 'updated_at'
               AND table_schema NOT IN ('pg_catalog','information_schema')
             ORDER BY table_schema, table_name
            """
        )
    ).all()
    out: list[dict[str, Any]] = []
    for schema, table in rows:
        try:
            newest = session.execute(
                text(f'SELECT max(updated_at) FROM "{schema}"."{table}"')
            ).scalar()
        except Exception:  # noqa: BLE001 — 表可能没有权限或已不存在
            continue
        if newest is None:
            continue
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=dt.timezone.utc)
        age_days = (_utcnow() - newest).total_seconds() / 86400.0
        if age_days >= stale_days:
            out.append(
                {
                    "table": f"{schema}.{table}",
                    "newest_updated_at": newest.isoformat(),
                    "age_days": round(age_days, 2),
                }
            )
    out.sort(key=lambda r: -r["age_days"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7, help="窗口天数（清单 ①②③）")
    ap.add_argument("--stale-days", type=int, default=7, help="陈旧阈值天数（清单 ④）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    jobs = _load_jobs()
    factory = _session_factory()
    session = factory()
    try:
        windowed = _job_rows(session, args.days)
        everything = _all_job_rows(session)
    finally:
        session.close()

    by_job: dict[str, list[Any]] = {}
    for r in everything:
        by_job.setdefault(r.job_name, []).append(r)

    # ① 注册了但窗口内没有任何行
    not_run = []
    for name in sorted(jobs):
        recent = [
            r for r in by_job.get(name, [])
            if r.started_at >= _utcnow() - dt.timedelta(days=args.days)
        ]
        if not recent:
            not_run.append({"job_name": name, "reason": "no rows in window"})

    # ② 永远 running（从无终态）
    zombies = []
    for r in everything:
        if r.status == "running":
            age = (_utcnow() - r.started_at).total_seconds()
            zombies.append(
                {
                    "job_name": r.job_name,
                    "started_at": r.started_at.isoformat(),
                    "age_seconds": round(age, 1),
                    "rows_total": r.rows_total,
                    "rows_inserted": r.rows_inserted,
                }
            )

    # ③ 有 succeeded 但 rows_inserted 恒为 0 且 rows_total > 0
    #    判据只看【窗口内】的 succeeded 行。2026-11-08 首版用了全部历史，
    #    结果 sync_source_cost_to_master 漏报 —— 它首次回填时写过行，于是
    #    "全部历史恒为 0" 不成立。而它恰恰是本脚本要抓的那一类。
    read_but_no_write = []
    window_by_job: dict[str, list[Any]] = {}
    for r in windowed:
        window_by_job.setdefault(r.job_name, []).append(r)
    for name in sorted(window_by_job):
        rows = window_by_job[name]
        succ = [r for r in rows if r.status == "succeeded"]
        if not succ:
            continue
        if all((r.rows_inserted or 0) == 0 for r in succ) and any(
            (r.rows_total or 0) > 0 for r in succ
        ):
            newest = succ[0]
            read_but_no_write.append(
                {
                    "job_name": name,
                    "succeeded_runs": len(succ),
                    "last_succeeded_at": newest.started_at.isoformat(),
                    "rows_total": newest.rows_total,
                    "rows_inserted": newest.rows_inserted,
                    "rows_updated": newest.rows_updated,
                    "note": "succeeded but wrote nothing — either idempotent-complete "
                            "or source is stale; distinguish via list 4",
                }
            )

    # ④ 源数据陈旧
    session2 = factory()
    try:
        stale = _stale_tables(session2, args.stale_days)
    finally:
        session2.close()

    report = {
        "generated_at": _utcnow().isoformat(),
        "window_days": args.days,
        "stale_days": args.stale_days,
        "registered_jobs": len(jobs),
        "list_1_not_run": not_run,
        "list_2_never_finished": zombies,
        "list_3_read_but_no_write": read_but_no_write,
        "list_4_stale_source_tables": stale,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    print(f"作业现状巡检 — 窗口 {args.days} 天 / 陈旧阈值 {args.stale_days} 天")
    print(f"生成时间: {report['generated_at']}")
    print(f"注册表作业数: {report['registered_jobs']}")
    print()
    print(f"① 注册了但窗口内没跑 ({len(not_run)})")
    for r in not_run:
        print(f"   {r['job_name']}")
    print()
    print(f"② 永远 running / 无终态 ({len(zombies)})")
    for r in sorted(zombies, key=lambda x: -x["age_seconds"]):
        print(f"   {r['job_name']:<45} age={r['age_seconds']:>10.1f}s "
              f"total={r['rows_total']} inserted={r['rows_inserted']}")
    print()
    print(f"③ succeeded 但一条没写 ({len(read_but_no_write)})")
    for r in read_but_no_write:
        print(f"   {r['job_name']:<45} succ={r['succeeded_runs']} "
              f"total={r['rows_total']} inserted={r['rows_inserted']}")
    print()
    print(f"④ 源表陈旧 ≥{args.stale_days} 天 ({len(stale)})")
    for r in stale:
        print(f"   {r['table']:<45} age={r['age_days']:>8.2f}d "
              f"newest={r['newest_updated_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
