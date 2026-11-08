#!/usr/bin/env python3
"""check_jobs_actually_run.py — 只读巡检：让「作业现状」可查询。

背景（2026-11-08 TTS-ERP 架构评审）：
  评审中反复出现「注释/文档说没跑、代码说在跑、库里又是另一回事」的三方矛盾。
  根因是**现状断言没有来自数据库**。本脚本把现状变成可查询的清单。

四张清单：
  ① 注册了但近 N 天没有任何行          —— 调度器注册了，作业从没被派发
  ② 有行但超过 2×interval 仍无终态      —— 进程被 SIGKILL / OOM，僵尸行
  ③ 近 N 次成功里有一半以上"读了没写"    —— 源数据陈旧 / 幂等完成，需靠 ④ 区分
  ④ 源数据陈旧                          —— 作业 succeeded，但源表长期未变

**只读保证**：脚本挂 `before_cursor_execute` 事件钩子，断言全程只出现
SELECT / SHOW 类语句；出现任何写语句立即中止并以非零码退出。这把
"只读"从注释变成机制。

用法：
    python scripts/check_jobs_actually_run.py [--days 7] [--stale-days 7]
                                              [--lookback 5]
                                              [--static-tables FILE]
                                              [--json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from typing import Any

REPO_HINT = "从仓库根目录运行，或确保 tts_erp_v2 可 import"

# 允许出现的语句前缀（只读）
_ALLOWED_SQL_PREFIXES = ("SELECT", "SHOW", "WITH", "EXPLAIN")
# 允许的表元数据查询（information_schema / pg_* 只读）
_ALLOWED_SQL_CONTAINS = ("information_schema", "pg_catalog", "pg_stat", "alembic_version")

_write_violations: list[str] = []


def _guard(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    """SQLAlchemy 事件钩子：拦截一切非只读语句。"""
    head = (statement or "").lstrip().upper()
    ok = head.startswith(_ALLOWED_SQL_PREFIXES) or any(
        token in head for token in _ALLOWED_SQL_CONTAINS
    )
    if not ok:
        _write_violations.append(head[:200])
        raise RuntimeError(
            f"只读巡检被中止：检测到非只读语句 -> {head[:120]}"
        )


def _install_readonly_guard(session_factory):  # noqa: ANN001
    """把只读守卫挂到 sessionmaker 实际使用的 Engine 上。

    2026-11-08 首版实现有 bug：靠 factory.kw.get("bind") 取 engine 常常拿到
    None，于是改用"包一层 factory"的写法，结果破坏了 sessionmaker 语义
    （session.execute 抛 DBAPI 包装错误而不是守卫错误）。
    现在改为：先建一个 session 取出真实的 bind engine，守卫挂在 engine 上，
    后续 session 全部共享该 engine ⇒ 守卫覆盖全部语句。
    """
    try:
        from sqlalchemy import event
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"cannot install read-only guard: {exc}\n")
        raise SystemExit(2)
    probe = session_factory()
    try:
        engine = probe.get_bind()
    finally:
        probe.close()
    event.listen(engine, "before_cursor_execute", _guard)
    return session_factory


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


def _jobs_source() -> dict[str, Any]:
    """自证：这份 JOBS 来自哪棵树、哪个 commit、连的是哪个库。"""
    import subprocess
    from pathlib import Path

    cwd = str(Path.cwd())
    head = None
    try:
        head = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or None
    except Exception:  # noqa: BLE001
        pass
    return {"cwd": cwd, "git_head": head}


def _db_identity(session) -> dict[str, Any]:
    """连接库标识（不含凭据）：datname + server address + 端口。"""
    from sqlalchemy import text

    try:
        row = session.execute(
            text("SELECT current_database(), current_user, inet_server_addr()::text, inet_server_port()")
        ).first()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    if not row:
        return {}
    return {
        "datname": row[0],
        "usename": row[1],
        "server_addr": row[2],
        "server_port": row[3],
    }


def _datname_by_process_group(session) -> list[dict[str, Any]]:
    """按进程/连接分组统计 datname。

    ⚠️ 2026-11-08 实测教训：全库 `count(DISTINCT datname)` 今天就不等于 1
    （残留的测试克隆库有 6 条连接）⇒ 那样写会天天误报。
    正确判据：**生产部署单元的进程组所连的 datname 唯一且等于生产库**。
    这里按 application_name / client_addr / usename 分组给出事实，不下结论。
    """
    from sqlalchemy import text

    try:
        rows = session.execute(
            text(
                """
                SELECT datname, usename, COALESCE(client_addr::text,'local') AS client,
                       COALESCE(application_name,'') AS app, count(*)
                  FROM pg_stat_activity
                 WHERE datname IS NOT NULL
                 GROUP BY 1,2,3,4
                 ORDER BY 1,2,3,4
                """
            )
        ).all()
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]
    return [
        {
            "datname": r[0], "usename": r[1], "client_addr": r[2],
            "application_name": r[3], "connections": r[4],
        }
        for r in rows
    ]


def _job_rows(session, since: dt.datetime) -> list[Any]:
    from sqlalchemy import select

    from tts_erp_v2.db.models import SyncJob

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


def _age_columns(session) -> list[tuple[str, str]]:
    from sqlalchemy import text

    rows = session.execute(
        text(
            """
            SELECT DISTINCT table_schema, table_name
              FROM information_schema.columns
             WHERE column_name IN ('updated_at','created_at')
               AND table_schema NOT IN ('pg_catalog','information_schema')
             ORDER BY table_schema, table_name
            """
        )
    ).all()
    out = []
    for schema, table in rows:
        cols = {
            r[0]
            for r in session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=:s AND table_name=:t "
                    "AND column_name IN ('updated_at','created_at')"
                ),
                {"s": schema, "t": table},
            ).all()
        }
        if "updated_at" in cols:
            out.append((f"{schema}.{table}", "updated_at"))
        elif "created_at" in cols:
            out.append((f"{schema}.{table}", "created_at"))
    return out


def _stale_tables(session, stale_days: int, now: dt.datetime) -> list[dict[str, Any]]:
    """④ 源数据陈旧。

    修正（2026-11-08）：不再硬编码只看 max(updated_at) —— **只 insert 不 update
    的 append-only 表（raw_records 等 PII 主仓）在那种口径下完全不可见**。
    现在按表声明可用的时间列：优先 updated_at，其次 created_at。
    """
    from sqlalchemy import text

    out: list[dict[str, Any]] = []
    for full_name, col in _age_columns(session):
        schema, table = full_name.split(".", 1)
        try:
            newest = session.execute(
                text(f'SELECT max("{col}") FROM "{schema}"."{table}"')
            ).scalar()
        except Exception:  # noqa: BLE001
            continue
        if newest is None:
            continue
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=dt.timezone.utc)
        age_days = (now - newest).total_seconds() / 86400.0
        if age_days >= stale_days:
            out.append(
                {
                    "table": full_name,
                    "age_column": col,
                    "newest": newest.isoformat(),
                    "age_days": round(age_days, 2),
                }
            )
    out.sort(key=lambda r: -r["age_days"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7, help="窗口天数（清单 ①②③）")
    ap.add_argument("--stale-days", type=int, default=7, help="陈旧阈值天数（清单 ④）")
    ap.add_argument("--lookback", type=int, default=5, help="清单③回看最近 N 次成功")
    ap.add_argument("--static-tables", default="", help="静态表豁免文件（每行一个表名）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    # 参数下限：--days 0 会让窗口为空、清单①把所有 job 误判为"没跑"
    if args.days < 1 or args.stale_days < 1 or args.lookback < 1:
        sys.stderr.write("--days / --stale-days / --lookback 必须 >= 1\n")
        return 2

    static_tables: set[str] = set()
    if args.static_tables:
        with open(args.static_tables, encoding="utf-8") as fh:
            static_tables = {
                line.strip() for line in fh if line.strip() and not line.startswith("#")
            }

    now = _utcnow()                      # ★ 单一时钟：整份报告用同一个 now
    jobs = _load_jobs()
    factory = _session_factory()
    factory = _install_readonly_guard(factory) or factory

    session = factory()
    try:
        windowed = _job_rows(session, now - dt.timedelta(days=args.days))
        everything = _all_job_rows(session)
        db_id = _db_identity(session)
        groups = _datname_by_process_group(session)
    finally:
        session.close()

    by_job: dict[str, list[Any]] = {}
    for r in everything:
        by_job.setdefault(r.job_name, []).append(r)

    window_since = now - dt.timedelta(days=args.days)

    # ① 注册了但窗口内没有任何行
    not_run = []
    for name in sorted(jobs):
        if not any(r.started_at >= window_since for r in by_job.get(name, [])):
            not_run.append({"job_name": name})

    # ② 超过 2×interval 仍无终态（阈值与 watchdog 规则(d) 一致）
    zombies = []
    for r in everything:
        if r.status != "running":
            continue
        interval = int(getattr(jobs.get(r.job_name), "interval_seconds", 0) or 0)
        threshold = interval * 2 if interval > 0 else None
        age = (now - r.started_at).total_seconds()
        if threshold is not None and age < threshold:
            continue          # 阈值内 = 可能正在正常跑，不报
        zombies.append(
            {
                "job_name": r.job_name,
                "started_at": r.started_at.isoformat(),
                "age_seconds": round(age, 1),
                "threshold_seconds": threshold,
                "rows_total": r.rows_total,
                "rows_inserted": r.rows_inserted,
                "reason": "no terminal state beyond 2x interval" if threshold else
                          "interval_seconds<=0 -> no threshold can be derived",
            }
        )

    # ③ 近 N 次成功里，"读了没写"占一半以上
    read_but_no_write = []
    for name in sorted(by_job):
        succ = [r for r in by_job[name] if r.status == "succeeded" and r.started_at >= window_since]
        if not succ:
            continue
        recent = succ[: args.lookback]
        zero = [r for r in recent if (r.rows_total or 0) > 0 and (r.rows_inserted or 0) == 0]
        if not zero:
            continue
        if len(zero) * 2 < len(recent):
            continue          # 多数次都写了 => 偶发零写不算
        read_but_no_write.append(
            {
                "job_name": name,
                "lookback": len(recent),
                "zero_insert_runs": len(zero),
                "last_succeeded_at": recent[0].started_at.isoformat(),
                "rows_total": recent[0].rows_total,
                "rows_inserted": recent[0].rows_inserted,
                "rows_updated": recent[0].rows_updated,
                "note": "succeeded but wrote nothing — distinguish 'idempotent-complete' "
                        "vs 'source stale' via list 4",
            }
        )

    # ④ 源表陈旧（排除静态表）
    session2 = factory()
    try:
        stale = [
            r for r in _stale_tables(session2, args.stale_days, now)
            if r["table"] not in static_tables
        ]
    finally:
        session2.close()

    report = {
        "generated_at": now.isoformat(),
        "window_days": args.days,
        "stale_days": args.stale_days,
        "lookback": args.lookback,
        "registered_jobs": len(jobs),
        "source": _jobs_source(),
        "db_identity": db_id,
        "datname_by_process_group": groups,
        "list_1_not_run": not_run,
        "list_2_never_finished": zombies,
        "list_3_read_but_no_write": read_but_no_write,
        "list_4_stale_source_tables": stale,
        "write_violations": _write_violations,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    print(f"作业现状巡检 — 窗口 {args.days} 天 / 陈旧阈值 {args.stale_days} 天 / 回看 {args.lookback} 次")
    print(f"生成时间(单一时钟): {report['generated_at']}")
    print(f"JOBS 来源: cwd={report['source']['cwd']}  git_head={report['source']['git_head']}")
    print(f"连接库: {db_id.get('datname')} @ {db_id.get('server_addr')}:{db_id.get('server_port')} "
          f"(user={db_id.get('usename')})")
    print(f"注册表作业数: {report['registered_jobs']}")
    print()
    print(f"① 注册了但窗口内没跑 ({len(not_run)})")
    for r in not_run:
        print(f"   {r['job_name']}")
    print()
    print(f"② 超过 2×interval 仍无终态 ({len(zombies)})")
    for r in sorted(zombies, key=lambda x: -x["age_seconds"]):
        thr = r["threshold_seconds"]
        print(f"   {r['job_name']:<45} age={r['age_seconds']:>10.1f}s "
              f"threshold={thr if thr is not None else 'N/A'} "
              f"total={r['rows_total']} inserted={r['rows_inserted']}")
    print()
    print(f"③ 近 {args.lookback} 次成功里'读了没写'占多数 ({len(read_but_no_write)})")
    for r in read_but_no_write:
        print(f"   {r['job_name']:<45} zero/{r['lookback']} "
              f"total={r['rows_total']} inserted={r['rows_inserted']}")
    print()
    print(f"④ 源表陈旧 ≥{args.stale_days} 天 ({len(stale)})")
    for r in stale:
        print(f"   {r['table']:<45} age={r['age_days']:>8.2f}d "
              f"[{r['age_column']}] newest={r['newest']}")
    print()
    print(f"只读守卫违规次数: {len(_write_violations)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
