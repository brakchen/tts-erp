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

**通则 18 · 判据三属性**（Chief 第八轮裁定，2026-11-09）：
  ① 结构判定 —— 靠语法结构，不靠子串/文本近似
  ② 双向自测 —— 应拦截 + 应放行，任一方向出错即非零退出
  ③ 跳过显形 —— 被跳过项数 = 一等指标（正常值 0）；每轮输出与上轮的增删差异；
                 `removed` 不得被解释成「好转」

**只读保证**：SQL 语句按【首 token】判定类型（SELECT/SHOW 才放行），
不靠整串扫词；启动时双向自测（7 条应拦截 + 9 条应放行）。

用法：
    python scripts/check_jobs_actually_run.py [--days 7] [--stale-days 7]
                                              [--lookback 5]
                                              [--static-tables FILE]
                                              [--state-file PATH]
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

# ── ① 结构判定：只看首 token（SQL 语法如此） ──────────────────────────
# 2026-11-09：原先是「整串扫写关键字」，踩了两次：
#   · 裸 "SECURITY" 关键字误伤本仓的 security schema ⇒ 7 张表被误判
#   · 子串白名单让「整条语句含 alembic_version」就放行 ⇒ W3
# ⇒ 现在：语句类型由【首 token】决定，不看整串里出现过什么词。
# EXPLAIN 单独不执行、EXPLAIN ANALYZE 会执行 ⇒ 两者都不放行。
# 代价：纯只读 CTE（WITH ... SELECT）会被拦（本脚本的 SQL 不含 CTE，无影响）。
_READONLY_HEADS = frozenset({"SELECT", "SHOW"})

_write_violations: list[str] = []


def _is_readonly(raw: str) -> bool:
    """结构判定：首 token ∈ {SELECT, SHOW} 且不是多语句。"""
    s = (raw or "").strip()
    if not s:
        return False
    if ";" in s.rstrip(";"):        # 多语句：去掉结尾分号后仍有分号
        return False
    head = s.split(None, 1)[0].rstrip(";").upper()
    return head in _READONLY_HEADS


def _guard(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    """SQLAlchemy 事件钩子：拦截一切非只读语句（结构判定）。"""
    raw = (statement or "").strip()
    if not _is_readonly(raw):
        _write_violations.append(raw.upper()[:200])
        raise RuntimeError(
            f"只读巡检被中止：检测到非只读语句 -> {raw.upper()[:120]}"
        )


# ── ② 双向自测 ───────────────────────────────────────────────────────
# 应拦截（7 条）：覆盖 @测试质量评审员 的 W1/W2/W3
_GUARD_BLOCK_CASES = (
    "INSERT INTO integration.sync_jobs (job_name) VALUES ('__guard_selftest__')",
    "UPDATE integration.sync_jobs SET rows_total = 0 WHERE id = -1",
    "DELETE FROM integration.sync_jobs WHERE job_name='alembic_version'",
    "WITH x AS (INSERT INTO integration.sync_jobs (job_name) VALUES ('x') RETURNING *) SELECT * FROM x",
    "EXPLAIN ANALYZE INSERT INTO integration.sync_jobs (job_name) VALUES ('x')",
    "SELECT 1; DROP TABLE integration.sync_jobs",
    "TRUNCATE integration.sync_jobs",
)
# 应放行（9 条）：覆盖 @测试质量评审员 的放行侧样例集
#   ★ 这一半以前完全没有 ⇒ "security" schema 误伤没有任何东西能拦住
_GUARD_ALLOW_CASES = (
    "SELECT 1",
    "SELECT version_num FROM alembic_version",
    "SELECT table_name FROM information_schema.columns WHERE column_name = 'updated_at'",
    'SELECT MAX("UPDATED_AT") FROM "SECURITY"."API_KEYS"',   # ← 本次踩的坑
    "SELECT setseed(0.5)",
    "SELECT * FROM t WHERE status = 'DELETE'",               # ← 字面量含写关键字
    "SELECT * FROM t WHERE note LIKE '%UPDATE%'",
    "SELECT * FROM t OFFSET 10",                              # ← OFFSET 不应命中 SET
    "SHOW transaction_isolation",
)


def _guard_self_test() -> tuple[list[str], list[str]]:
    """双向自测。返回 (漏网的写语句, 被误拦的只读语句)。

    只断言"正常巡检无违规"是不够的 —— 正常巡检本来就不写库，
    **守卫坏了也会绿**；反过来，只测拦截侧也挡不住误伤。
    """
    leaked: list[str] = []
    blocked_ok = []
    for stmt in _GUARD_BLOCK_CASES:
        try:
            _guard(None, None, stmt, None, None, False)
            leaked.append(stmt)
        except RuntimeError:
            blocked_ok.append(stmt)
    false_positive: list[str] = []
    for stmt in _GUARD_ALLOW_CASES:
        try:
            _guard(None, None, stmt, None, None, False)
        except RuntimeError:
            false_positive.append(stmt)
    return leaked, false_positive


def _install_readonly_guard(session_factory):  # noqa: ANN001
    """把只读守卫挂到 sessionmaker 实际使用的 Engine 上。"""
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
    """连接库标识（不含凭据）：datname / user / server addr:port。"""
    from sqlalchemy import text

    try:
        row = session.execute(
            text("SELECT current_database(), current_user, "
                 "inet_server_addr()::text, inet_server_port()")
        ).first()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    if not row:
        return {}
    return {
        "datname": row[0], "usename": row[1],
        "server_addr": row[2], "server_port": row[3],
    }


def _datname_by_process_group(session) -> list[dict[str, Any]]:
    """按进程/连接分组统计 datname（不做全库 count）。"""
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
        {"datname": r[0], "usename": r[1], "client_addr": r[2],
         "application_name": r[3], "connections": r[4]}
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


def _stale_tables(session, stale_days: int, now: dt.datetime):
    """④ 源数据陈旧。返回 (陈旧表列表, 查询失败列表)。

    ⚠️ 查询失败【不得静默跳过】：守卫误判 → 抛错 → 被宽 except 吞掉 →
    该表从清单里静默消失（实测 ④ 从 23 张掉到 21 张，零报错）。
    ⇒ 被跳过的表必须显形。
    """
    from sqlalchemy import text

    out: list[dict[str, Any]] = []
    query_failures: list[dict[str, Any]] = []
    for full_name, col in _age_columns(session):
        schema, table = full_name.split(".", 1)
        try:
            newest = session.execute(
                text(f'SELECT max("{col}") FROM "{schema}"."{table}"')
            ).scalar()
        except Exception as exc:  # noqa: BLE001
            query_failures.append({"table": full_name, "error": str(exc)[:200]})
            continue
        if newest is None:
            continue
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=dt.timezone.utc)
        age_days = (now - newest).total_seconds() / 86400.0
        if age_days >= stale_days:
            out.append({
                "table": full_name, "age_column": col,
                "newest": newest.isoformat(), "age_days": round(age_days, 2),
            })
    out.sort(key=lambda r: -r["age_days"])
    return out, query_failures


def _load_previous(state_path: str) -> dict[str, Any]:
    try:
        with open(state_path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def _save_state(state_path: str, report: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
        slim = {
            "generated_at": report["generated_at"],
            "stale_tables": [r["table"] for r in report["list_4_stale_source_tables"]],
            "skipped_count": report["skipped"]["count"],
        }
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(slim, fh, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"cannot save state file: {exc}\n")


def _delta_vs_previous(
    current: list[str],
    previous: dict[str, Any],
    *,
    excluded: set[str] | None = None,
    skipped_tables: set[str] | None = None,
) -> dict[str, Any]:
    """与上一轮的增删差异。

    ★ `removed` 的原因必须是【三态判定】，不是自由文本（@测试质量评审员 §二）：
        resolved           —— 源变新（真好转）
        excluded_by_config —— 在排除清单里（有意排除）
        query_failed       —— 在被跳过的表里（**看不见了** ⚠ 不是好转）
    ⇒ 第三态一旦出现 ⇒ 本轮必须报 finding。
    """
    prev = set(previous.get("stale_tables") or [])
    cur = set(current)
    added = sorted(cur - prev)
    removed = sorted(prev - cur)
    excluded = excluded or set()
    skipped_tables = skipped_tables or set()

    states: dict[str, str] = {}
    for t in removed:
        if t in skipped_tables:
            states[t] = "query_failed"
        elif t in excluded:
            states[t] = "excluded_by_config"
        else:
            states[t] = "resolved"
    unexplained = sorted(t for t, st in states.items() if st == "resolved")
    return {
        "added": added,
        "removed": removed,
        "removed_states": states,
        "removed_reason": (
            "none" if not removed
            else ("存在 query_failed：被跳过的表不是「好转了」，是「看不见了」"
                  if any(st == "query_failed" for st in states.values())
                  else ("全部来自排除清单" if removed and all(st == "excluded_by_config" for st in states.values())
                        else "判定为源变新（resolved）"))
        ),
        "removed_unexplained": unexplained,
        "previous_generated_at": previous.get("generated_at"),
        "previous_skipped_count": previous.get("skipped_count"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7, help="窗口天数（清单 ①②③）")
    ap.add_argument("--stale-days", type=int, default=7, help="陈旧阈值天数（清单 ④）")
    ap.add_argument("--lookback", type=int, default=5, help="清单③回看最近 N 次成功")
    ap.add_argument("--static-tables", default="", help="静态表豁免文件（每行一个表名）")
    ap.add_argument("--state-file", default="", help="上轮状态文件（用于增删差异）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    if args.days < 1 or args.stale_days < 1 or args.lookback < 1:
        sys.stderr.write("--days / --stale-days / --lookback 必须 >= 1\n")
        return 2

    # ★ ② 双向自测：漏网 ⇒ 3，误伤 ⇒ 4
    leaked, false_positive = _guard_self_test()
    if leaked:
        sys.stderr.write(
            "只读守卫自测失败（方向一）：以下写语句未被拦截\n"
            + "\n".join(f"  - {s}" for s in leaked) + "\n")
        return 3
    if false_positive:
        sys.stderr.write(
            "只读守卫自测失败（方向二）：以下只读语句被误拦\n"
            + "\n".join(f"  - {s}" for s in false_positive) + "\n")
        return 4

    static_tables: set[str] = set()
    if args.static_tables:
        with open(args.static_tables, encoding="utf-8") as fh:
            static_tables = {
                line.strip() for line in fh if line.strip() and not line.startswith("#")
            }

    now = _utcnow()
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

    not_run = []
    for name in sorted(jobs):
        if not any(r.started_at >= window_since for r in by_job.get(name, [])):
            not_run.append({"job_name": name})

    zombies = []
    for r in everything:
        if r.status != "running":
            continue
        interval = int(getattr(jobs.get(r.job_name), "interval_seconds", 0) or 0)
        threshold = interval * 2 if interval > 0 else None
        age = (now - r.started_at).total_seconds()
        if threshold is not None and age < threshold:
            continue
        zombies.append({
            "job_name": r.job_name, "started_at": r.started_at.isoformat(),
            "age_seconds": round(age, 1), "threshold_seconds": threshold,
            "rows_total": r.rows_total, "rows_inserted": r.rows_inserted,
            "reason": "no terminal state beyond 2x interval" if threshold
                      else "interval_seconds<=0 -> no threshold can be derived",
        })

    read_but_no_write = []
    window_by_job: dict[str, list[Any]] = {}
    for r in windowed:
        window_by_job.setdefault(r.job_name, []).append(r)
    for name in sorted(window_by_job):
        rows = window_by_job[name]
        succ = [r for r in rows if r.status == "succeeded" and r.started_at >= window_since]
        if not succ:
            continue
        recent = succ[: args.lookback]
        zero = [r for r in recent if (r.rows_total or 0) > 0 and (r.rows_inserted or 0) == 0]
        if not zero or len(zero) * 2 < len(recent):
            continue
        read_but_no_write.append({
            "job_name": name, "lookback": len(recent),
            "zero_insert_runs": len(zero),
            "last_succeeded_at": recent[0].started_at.isoformat(),
            "rows_total": recent[0].rows_total,
            "rows_inserted": recent[0].rows_inserted,
            "rows_updated": recent[0].rows_updated,
            "note": "succeeded but wrote nothing — distinguish 'idempotent-complete' "
                    "vs 'source stale' via list 4",
        })

    session2 = factory()
    try:
        stale, stale_query_failures = _stale_tables(session2, args.stale_days, now)
        stale = [r for r in stale if r["table"] not in static_tables]
    finally:
        session2.close()

    previous = _load_previous(args.state_file) if args.state_file else {}
    delta = _delta_vs_previous(
        [r["table"] for r in stale],
        previous,
        excluded=static_tables,
        skipped_tables={i["table"] for i in stale_query_failures},
    )

    report = {
        "generated_at": now.isoformat(),
        "window_days": args.days, "stale_days": args.stale_days, "lookback": args.lookback,
        "registered_jobs": len(jobs),
        "source": _jobs_source(),
        "db_identity": db_id,
        "datname_by_process_group": groups,
        "list_1_not_run": not_run,
        "list_2_never_finished": zombies,
        "list_3_read_but_no_write": read_but_no_write,
        "list_4_stale_source_tables": stale,
        "skipped": {"count": len(stale_query_failures), "items": stale_query_failures},
        # ★ 脚本本来就知道自己排除了哪些表，却没输出 ⇒ 清单变短时
        #   「排除导致」与「真好转」在输出上不可分辨（@测试质量评审员 §一）
        "excluded": {
            "count": len(static_tables),
            "items": [
                {"table": t, "reason": "static-table-exempt",
                 "source": args.static_tables or "(inline)", "added_at": now.isoformat()}
                for t in sorted(static_tables)
            ],
        },
        "delta_vs_previous": delta,
        "write_violations": _write_violations,
        "guard_self_test": {
            "block_cases": len(_GUARD_BLOCK_CASES),
            "allow_cases": len(_GUARD_ALLOW_CASES),
            "leaked": leaked, "false_positive": false_positive,
        },
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"作业现状巡检 — 窗口 {args.days} 天 / 陈旧阈值 {args.stale_days} 天 / 回看 {args.lookback} 次")
        print(f"生成时间(单一时钟): {report['generated_at']}")
        print(f"JOBS 来源: cwd={report['source']['cwd']}  git_head={report['source']['git_head']}")
        print(f"连接库: {db_id.get('datname')} @ {db_id.get('server_addr')}:{db_id.get('server_port')} (user={db_id.get('usename')})")
        print(f"注册表作业数: {report['registered_jobs']}")
        print()
        print(f"① 注册了但窗口内没跑 ({len(not_run)})")
        for r in not_run:
            print(f"   {r['job_name']}")
        print()
        print(f"② 超过 2×interval 仍无终态 ({len(zombies)})")
        for r in sorted(zombies, key=lambda x: -x["age_seconds"]):
            thr = r["threshold_seconds"]
            print(f"   {r['job_name']:<45} age={r['age_seconds']:>10.1f}s threshold={thr if thr is not None else 'N/A'} total={r['rows_total']} inserted={r['rows_inserted']}")
        print()
        print(f"③ 近 {args.lookback} 次成功里'读了没写'占多数 ({len(read_but_no_write)})")
        for r in read_but_no_write:
            print(f"   {r['job_name']:<45} zero/{r['lookback']} total={r['rows_total']} inserted={r['rows_inserted']}")
        print()
        print(f"④ 源表陈旧 ≥{args.stale_days} 天 ({len(stale)})")
        for r in stale:
            print(f"   {r['table']:<45} age={r['age_days']:>8.2f}d [{r['age_column']}] newest={r['newest']}")
        print()
        print(f"被跳过项数（一等指标，正常值 0）: {report['skipped']['count']}")
        for r in report["skipped"]["items"]:
            print(f"   {r['table']}: {r['error']}")
        print(f"与上轮差异: added={len(delta['added'])} removed={len(delta['removed'])}")
        if delta["removed"]:
            print(f"   removed 三态: {delta['removed_states']}")
            print(f"   ⚠ removed 不得被解释成「好转」：{delta['removed_reason']}")
        # ★ removed ∩ skipped = ∅ —— 被跳过的表不是「好转了」，是「看不见了」
        bad = sorted(set(delta["removed"]) & {i["table"] for i in stale_query_failures})
        if bad:
            print(f"   ⚠⚠ removed ∩ skipped ≠ ∅：{bad} ⇒ 有表是被跳过的，不是好转")
        else:
            print("   removed ∩ skipped = ∅ ✓")
        print(f"只读守卫违规次数: {len(_write_violations)}")
        print(f"守卫自测: 拦截样例 {len(_GUARD_BLOCK_CASES)} 条 / 放行样例 {len(_GUARD_ALLOW_CASES)} 条 "
              f"→ 漏网 {len(leaked)} / 误伤 {len(false_positive)}")

    if args.state_file:
        _save_state(args.state_file, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
