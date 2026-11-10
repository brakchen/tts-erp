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
                                              [--expect-db tts_erp]
                                              [--expect-code-root /path/to/tree]
                                              [--unit tts-erp]
                                              [--units a,b,c]
                                              [--json]

身份断言（三条，全部在【任何业务查询之前】失败即非零退出）：
    ① 连库：current_database() 必须 == --expect-db（事实 vs DSN 声称）
    ② 代码路径：服务加载的 tts_erp_v2 必须来自 --expect-code-root
       （判据 = find_spec('tts_erp_v2').origin，不是 git HEAD / 目录名）
    ③ 世代：进程自证的 TTS_ERP_DEPLOYED_COMMIT 必须 == 磁盘 HEAD
       （四个 unit 同一路径但可分属不同代际，路径断言抓不到）

退出码：
    0  巡检完成（清单里有发现也返回 0 —— 发现项看输出，别拿退出码当"没问题"）
    2  参数错误
    3  只读守卫自测方向一失败（漏网写语句）
    4  只读守卫自测方向二失败（误伤只读语句）
    5  身份断言失败（连错库 / 跑错树 / 世代不一致）
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
# 自测期间不把【自测自己的拦截样例】记进违规计数 ——
# 否则"只读守卫违规次数"恒等于自测样例数（7），永远不是 0 ⇒ 零信号
# （恒红即废：正常值不是 0 的指标不是指标）。
_self_test_mode = False


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
        if not _self_test_mode:
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
    global _self_test_mode
    _self_test_mode = True
    try:
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
    finally:
        _self_test_mode = False
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
    return {
        "cwd": cwd,
        "git_head": head,
        "note": "cwd/git_head 是【声称】不是判据；判据见 identity.code.origin",
    }


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


# ── 身份断言（2026-11-10）────────────────────────────────────────────
# 两个真实事故，各对应一个不可替代的判据：
#
#   ① 连错库：`docker exec postgres psql -U postgres` 的【默认库就是维护库
#      postgres】，不是业务库 tts_erp。影子库里 integration.sync_jobs
#      表存在但 0 行，真库同表 100,057 行 ⇒ 同一句
#      `select count(*) ... where status='running'` 在两个库分别返 0 和 7
#      ⇒ 「僵尸 running 已清零」这个错误结论就是这么来的（连错库的人自己
#      都被骗过）。
#      ⇒ 判据 = `current_database()`（【事实】），不是 DSN 里写了什么（【声称】）。
#
#   ② 跑错树：git HEAD / 目录名 / drop-in 里声明的 commit 都不能作判据
#      （实测四个服务分属两个代际：主 API 与 publish 跑 11-06 的 84ec0a49，
#      sync 跑 11-08 的 f88fb713，release HEAD 是 11-09 的 1a319978）。
#      cwd 决定 sys.path[0]，而 editable finder 排在 PathFinder 之后
#      ⇒ cwd 先命中 ⇒ 加载哪棵树由 cwd 决定。
#      ⇒ 判据 = `importlib.util.find_spec('tts_erp_v2').origin`（【事实】）。
#
# ⇒ 两条断言都必须在【任何业务查询之前】失败即非零退出。
#   只打印不退出 = 把「我连错库了」伪装成「巡检没发现问题」——
#   本脚本早就有 _db_identity()，但它只采集不判定，且采集时机在查完数据之后。
EXIT_USAGE = 2
EXIT_GUARD_LEAK = 3
EXIT_GUARD_FALSE_POSITIVE = 4
EXIT_IDENTITY = 5


def _dsn_dbname() -> str | None:
    """生效配置里 TTS_ERP_DB_URL 的库名段（只取库名，绝不输出凭据）。

    来源优先级是 dotenv > runtime 文件 > os.environ（runtime_settings.get），
    所以只读 os.environ 常常拿到 None —— 那不是"没有 DSN"，
    是"DSN 不在 env 里"。这里按生效顺序取，才对得上"声称"。
    """
    raw = None
    try:
        from tts_erp_v2.runtime_settings import require_setting

        raw = require_setting("TTS_ERP_DB_URL")
    except Exception:  # noqa: BLE001
        raw = os.environ.get("TTS_ERP_DB_URL")
    if not raw:
        return None
    if "://" in raw:
        from urllib.parse import urlparse

        name = (urlparse(raw).path or "").lstrip("/").split("?")[0]
        return name or None
    for token in raw.split():            # libpq 键值形态：... dbname=tts_erp ...
        if token.startswith("dbname="):
            return token.split("=", 1)[1] or None
    return None


def _assert_db_identity(session, expected_db: str) -> dict[str, Any]:
    """连库身份断言：current_database() 必须等于期望库名。

    返回身份字典供报告使用；不匹配 ⇒ 打印可核对的差异并以 EXIT_IDENTITY 退出。
    """
    from sqlalchemy import text

    try:
        row = session.execute(
            text("SELECT current_database(), current_user, "
                 "inet_server_addr()::text, inet_server_port()")
        ).first()
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"⛔ 身份断言失败（连库）：读不到 current_database()：{exc}\n"
            f"   ⇒ 无法证明连的是哪个库 ⇒ 拒绝继续巡检（exit {EXIT_IDENTITY}）\n"
        )
        raise SystemExit(EXIT_IDENTITY)
    if not row:
        sys.stderr.write(
            f"⛔ 身份断言失败（连库）：current_database() 没有返回值\n"
            f"   ⇒ 拒绝继续巡检（exit {EXIT_IDENTITY}）\n"
        )
        raise SystemExit(EXIT_IDENTITY)

    actual = row[0]
    identity = {
        "datname": actual,
        "usename": row[1],
        "server_addr": row[2],
        "server_port": row[3],
        "dsn_dbname": _dsn_dbname(),
        "expected_datname": expected_db,
        "asserted": True,
    }
    if actual != expected_db:
        sys.stderr.write(
            "⛔ 身份断言失败（连错库）\n"
            f"   期望 current_database() = {expected_db!r}\n"
            f"   实际 current_database() = {actual!r}\n"
            f"   DSN 声称的库名段       = {identity['dsn_dbname']!r}（声称 ≠ 事实）\n"
            f"   服务端 {identity['server_addr']}:{identity['server_port']}"
            f"  user={identity['usename']}\n"
            f"   ⇒ 拒绝继续巡检（exit {EXIT_IDENTITY}）。"
            f"若你要巡检的确实是 {actual!r}，用 --expect-db {actual!r} 显式声明。\n"
        )
        raise SystemExit(EXIT_IDENTITY)
    return identity


def _systemctl_user_show(unit: str, prop: str) -> str | None:
    """读 user scope 的 unit 属性；不在 systemd 环境或属性为空时返回 None。"""
    import subprocess

    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", prop, "--value"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:  # noqa: BLE001
        return None
    value = (out.stdout or "").strip()
    return value or None


def _extract_interpreter(exec_start: str) -> str | None:
    """从 ExecStart 里取出 python 解释器路径（systemctl 的两种形态都吃）。"""
    import re

    if not exec_start:
        return None
    match = re.search(r"path=([^ ;}]+)", exec_start)
    candidate = match.group(1) if match else exec_start.split()[0]
    if candidate and "python" in os.path.basename(candidate):
        return candidate
    return None


def _service_code_identity(unit: str) -> dict[str, Any]:
    """服务实际加载的 tts_erp_v2 来自哪棵树（find_spec 事实）。

    复现服务真实的加载条件：服务自己的 WorkingDirectory + 服务自己的解释器。
    """
    import subprocess
    from pathlib import Path

    wd = _systemctl_user_show(unit, "WorkingDirectory")
    exec_start = _systemctl_user_show(unit, "ExecStart") or ""
    interp = _extract_interpreter(exec_start) or sys.executable
    origin = None
    error = None
    if wd and Path(wd).is_dir():
        try:
            out = subprocess.run(
                [
                    interp, "-c",
                    "import importlib.util,sys;"
                    "s=importlib.util.find_spec('tts_erp_v2');"
                    "sys.stdout.write(s.origin if s else '')",
                ],
                capture_output=True, text=True, timeout=30, cwd=wd,
            )
            origin = (out.stdout or "").strip() or None
            if out.returncode != 0:
                error = (out.stderr or "").strip()[-400:]
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
    else:
        error = f"unit {unit} 的 WorkingDirectory 不可用: {wd!r}"
    return {
        "unit": unit,
        "working_directory": wd,
        "interpreter": interp,
        "exec_start": exec_start,
        "origin": origin,
        "error": error,
    }


def _assert_code_identity(expected_root: str | None, unit: str) -> dict[str, Any]:
    """代码身份断言：服务加载的 tts_erp_v2 必须来自 expected_root。"""
    info = _service_code_identity(unit)
    if not expected_root:
        expected_root = info.get("working_directory")
    expected_root = os.path.abspath(expected_root) if expected_root else None
    origin = info.get("origin")

    ok = False
    if origin and expected_root:
        try:
            ok = (
                os.path.commonpath([os.path.abspath(origin), expected_root])
                == expected_root
            )
        except ValueError:
            ok = False
    info["expected_root"] = expected_root
    info["asserted"] = bool(expected_root and origin)
    info["ok"] = ok

    # ★ 巡检脚本【自己】加载的是哪棵树 —— 第四个身份，同样必须判。
    #   实测：把脚本放 /tmp 跑 ⇒ sys.path[0] 是【脚本所在目录】而不是 cwd ⇒
    #   `import tts_erp_v2` 被 editable 映射接管 ⇒ 加载了主 checkout 那棵
    #   【残缺的树】（缺 runtime_settings.py）⇒ 连 DB URL 都读不到。
    #   ⇒ 巡检逻辑和被巡检对象必须是同一棵树，否则巡检结论是另一份代码的结论。
    local_origin = None
    try:
        import importlib.util

        spec = importlib.util.find_spec("tts_erp_v2")
        local_origin = spec.origin if spec else None
    except Exception as exc:  # noqa: BLE001
        local_origin = f"<error: {exc}>"
    info["local_origin"] = local_origin
    local_ok = False
    if local_origin and expected_root:
        try:
            local_ok = (
                os.path.commonpath(
                    [os.path.abspath(local_origin), expected_root]
                )
                == expected_root
            )
        except ValueError:
            local_ok = False
    info["local_ok"] = local_ok

    if not ok:
        sys.stderr.write(
            "⛔ 身份断言失败（跑错树）\n"
            f"   unit             = {unit}\n"
            f"   WorkingDirectory = {info.get('working_directory')!r}\n"
            f"   解释器           = {info.get('interpreter')!r}\n"
            f"   实际加载 origin  = {origin!r}\n"
            f"   期望代码根       = {expected_root!r}\n"
            f"   报错             = {info.get('error')!r}\n"
            f"   ⇒ 拒绝继续巡检（exit {EXIT_IDENTITY}）。\n"
            "   判据说明：git HEAD / 目录名 / drop-in 声明的 commit 都不是判据；\n"
            "   唯一判据是 find_spec('tts_erp_v2').origin（cwd 决定加载哪棵树）。\n"
        )
        raise SystemExit(EXIT_IDENTITY)
    if not local_ok:
        sys.stderr.write(
            "⛔ 身份断言失败（巡检脚本自己跑在错误的树上）\n"
            f"   巡检脚本加载的 origin = {local_origin!r}\n"
            f"   期望代码根             = {expected_root!r}\n"
            f"   ⇒ 拒绝继续巡检（exit {EXIT_IDENTITY}）。\n"
            f"   原因：sys.path[0] 是【脚本所在目录】而不是 cwd —— 脚本放在\n"
            f"   期望树之外运行时，import 会被 editable 映射接管、加载另一棵树\n"
            f"   （实测会加载主 checkout 那棵缺 runtime_settings.py 的残缺树）。\n"
            f"   修法：PYTHONPATH={expected_root} {sys.executable} <脚本路径>\n"
        )
        raise SystemExit(EXIT_IDENTITY)
    return info


# ── 身份断言 ③：世代自证 ────────────────────────────────────────────
# 路径断言抓不到「同一个目录里的不同代际」：发布动作是 mv + cp -a 原地覆盖，
# 四个 unit 的 WorkingDirectory 是同一条路径，但进程按启动时刻加载了不同内容。
# 实测四个服务分属两个代际（主 API / publish 跑 11-06 的 84ec0a49，
# sync 跑 11-08 的 f88fb713，磁盘 HEAD 是 11-09 的 1a319978）。
# ⇒ 判据 = 三方对照：
#     进程 /proc/<pid>/environ 里的 TTS_ERP_DEPLOYED_COMMIT（进程自证）
#     vs 磁盘 HEAD（树上现在是什么）
#     vs drop-in 文件里声明的值（systemctl --user cat，不受 daemon-reload 缓存影响）
def _unit_env_from_dropin(unit: str) -> str | None:
    """从 unit 文件（含 drop-in）里读声明的 TTS_ERP_DEPLOYED_COMMIT。"""
    import subprocess

    try:
        out = subprocess.run(
            ["systemctl", "--user", "cat", unit],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:  # noqa: BLE001
        return None
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("Environment=TTS_ERP_DEPLOYED_COMMIT="):
            return line.split("=", 2)[2].strip() or None
    return None


def _pid_env(pid: str | None, key: str) -> str | None:
    """读进程环境变量（只读 /proc，不回显任何口令类变量）。"""
    if not pid or pid == "0":
        return None
    try:
        with open(f"/proc/{pid}/environ", "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    prefix = key.encode() + b"="
    for item in raw.split(b"\0"):
        if item.startswith(prefix):
            return item.split(b"=", 1)[1].decode("utf-8", "replace")
    return None


def _git_head(path: str | None) -> str | None:
    """目录对应的 git HEAD（拿不到就返回 None，不猜）。"""
    if not path:
        return None
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", path, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:  # noqa: BLE001
        return None
    return (out.stdout or "").strip() or None


def _generation_identity(units: list[str]) -> dict[str, Any]:
    """进程世代自证：进程实际加载的 commit vs 磁盘 HEAD vs drop-in 声明。"""
    items: list[dict[str, Any]] = []
    failures: list[str] = []
    skipped: list[str] = []
    for unit in units:
        state = _systemctl_user_show(unit, "ActiveState") or "unknown"
        pid = _systemctl_user_show(unit, "MainPID")
        wd = _systemctl_user_show(unit, "WorkingDirectory")
        disk_head = _git_head(wd)
        process_commit = _pid_env(pid, "TTS_ERP_DEPLOYED_COMMIT")
        declared = _unit_env_from_dropin(unit)
        item: dict[str, Any] = {
            "unit": unit,
            "active_state": state,
            "pid": pid,
            "working_directory": wd,
            "disk_head": disk_head,
            "process_deployed_commit": process_commit,
            "dropin_declared_commit": declared,
        }
        if state != "active":
            item["skipped"] = f"unit not active (ActiveState={state})"
            skipped.append(unit)
        elif not process_commit:
            item["mismatch"] = "进程环境里没有 TTS_ERP_DEPLOYED_COMMIT（进程无法自证身份）"
            failures.append(unit)
        elif disk_head and process_commit != disk_head:
            item["mismatch"] = "进程加载的 commit ≠ 磁盘 HEAD（跑的是旧代际）"
            failures.append(unit)
        elif declared and disk_head and declared != disk_head:
            item["mismatch"] = "drop-in 声明的 commit ≠ 磁盘 HEAD（改了没重启）"
            failures.append(unit)
        else:
            item["ok"] = True
        items.append(item)
    return {"units": items, "failures": failures, "skipped": skipped}


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
    ap.add_argument(
        "--expect-db",
        default=os.environ.get("TTS_ERP_EXPECT_DB", "tts_erp"),
        help="期望连上的库名；事实判据 current_database() 必须等于它",
    )
    ap.add_argument(
        "--expect-code-root",
        default=os.environ.get("TTS_ERP_EXPECT_CODE_ROOT", ""),
        help="期望的代码根目录；留空则从 unit 的 WorkingDirectory 推导",
    )
    ap.add_argument(
        "--unit",
        default=os.environ.get("TTS_ERP_UNIT", "tts-erp"),
        help="定义生产代码树的 unit（user scope）",
    )
    ap.add_argument(
        "--units",
        default=os.environ.get(
            "TTS_ERP_UNITS",
            "tts-erp,tts-erp-sync,tts-erp-publish,tts-erp-watchdog",
        ),
        help="要做世代自证的 unit 列表（逗号分隔，user scope）",
    )
    ap.add_argument(
        "--skip-generation",
        action="store_true",
        help="跳过世代自证（发布窗口中途用）；跳过会记进报告，不是静默跳过",
    )
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

    # ★ 身份断言 ②：代码树。必须排在任何 DB 连接之前 ——
    #   连错代码查出来的结论，和连错库一样是假的。
    code_identity = _assert_code_identity(args.expect_code_root or None, args.unit)

    # ★ 身份断言 ③：世代自证（路径相同但内容不同代 ⇒ 路径断言抓不到）
    units = [u.strip() for u in args.units.split(",") if u.strip()]
    if args.skip_generation:
        generation_identity = {
            "units": [],
            "failures": [],
            "skipped": units,
            "skipped_reason": "--skip-generation（发布窗口中途显式跳过；正常值 0）",
        }
    else:
        generation_identity = _generation_identity(units)
    if generation_identity["failures"]:
        detail = "\n".join(
            f"   - {i['unit']}: {i.get('mismatch')}"
            f"（进程自证={i.get('process_deployed_commit')}"
            f" 磁盘HEAD={i.get('disk_head')}"
            f" drop-in声明={i.get('dropin_declared_commit')}）"
            for i in generation_identity["units"]
            if i.get("mismatch")
        )
        sys.stderr.write(
            "⛔ 身份断言失败（世代不一致）\n"
            + detail + "\n"
            f"   ⇒ 拒绝继续巡检（exit {EXIT_IDENTITY}）。\n"
            "   判据说明：四个 unit 的 WorkingDirectory 是同一条路径（发布用 mv+cp -a\n"
            "   原地覆盖），路径断言抓不到代际差异；代际只能靠进程自证的 commit 对照\n"
            "   磁盘 HEAD 来判。\n"
        )
        raise SystemExit(EXIT_IDENTITY)

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
        # ★ 身份断言 ①：连库。必须排在【第一条业务查询之前】——
        #   旧版把 _db_identity() 放在查完数据之后，于是「连错库」时结论
        #   照样出得来，还带着「僵尸已清零」的假象。
        db_id = _assert_db_identity(session, args.expect_db)
        windowed = _job_rows(session, now - dt.timedelta(days=args.days))
        everything = _all_job_rows(session)
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
        "identity": {
            "db": db_id,
            "code": code_identity,
            "generation": generation_identity,
            "note": "三个身份断言都在业务查询之前失败即非零退出（exit 5）",
        },
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
        print(f"身份断言① 连库: current_database()={db_id.get('datname')} (期望 {db_id.get('expected_datname')}) ✓")
        print(f"身份断言② 代码: origin={code_identity.get('origin')} (期望根 {code_identity.get('expected_root')}) ✓")
        print(f"身份断言③ 世代: 核对 {len(generation_identity['units'])} 个 unit"
              f"，不一致 {len(generation_identity['failures'])}，跳过 {len(generation_identity['skipped'])}")
        for i in generation_identity["units"]:
            print(f"   {i['unit']:<26} 进程={str(i.get('process_deployed_commit'))[:12]}"
                  f" 磁盘={str(i.get('disk_head'))[:12]}"
                  f" 声明={str(i.get('dropin_declared_commit'))[:12]}"
                  f" [{i.get('mismatch') or i.get('skipped') or 'OK'}]")
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
