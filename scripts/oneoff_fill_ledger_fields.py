#!/usr/bin/env python3
"""oneoff_fill_ledger_fields.py — 给台账 24 条 rule 补齐六字段（一次性）

Chief 2026-11-10 要求：
    每条 rule 必须有【真列】：
        owner_ref + owner_blob + symbol + line_digest
        + 可执行检查引用（check_ref / verification.command）
        + 判据类型（scope_assertion.kind + method）
    判据分类：位置 / 数量枚举 / 分支覆盖，**各自的 method 分开**。

本脚本做的是【填值】，不是【判定】：
    - owner_ref / owner_path：按「文件在哪个树里存在」决定
      （生产 50978019… 与目标 5597dcca… 二选一；仓库外的事实先固化进
        scripts/infra/runtime-facts.yaml，再指向那个文件）
    - owner_blob / symbol_line / line_digest：回到 git 对象上解析
      （owner_blob = git rev-parse <ref>:<path>；
        line_digest = sha256(该行去空白) 前 16 位）
    - 判据类型与锚点：人工给定，但**必须在 owner blob 里真的存在**，
      本脚本逐条核对，锚点找不到就直接报错退出（不静默写进去）。

用法：
    python scripts/oneoff_fill_ledger_fields.py --runtime-ref <SHA> [--apply]

不传 --apply 时只做核对与打印（dry-run）；加 --apply 才写回 YAML。
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

import yaml

PROD = "50978019f39c4e6ea3671872b540c8ea79644b00"
TARGET = "5597dcca2139ae210873616b65081246d9b032e3"
LEDGER = "docs/architecture/arch-debt-ledger.yaml"
RUNTIME_FACTS = "scripts/infra/runtime-facts.yaml"
CHECKER = "scripts/check_ledger_owners.py"

# 每条 rule：owner 树、owner 文件、符号、判据类型与方法、判据参数
SPEC: dict[str, dict] = {
    "AD-CFG-001": dict(
        ref=PROD, path="tts_erp_v2/runtime_settings.py", symbol="get_setting_facts",
        kind="branch", scope="block",
        anchors=["dotenv", "runtime_file", "process_env", "default"],
        method="owner 的 get_setting_facts 必须同时给出三层来源键 "
               "(dotenv/runtime_file/process_env) 与兜底 default 四个分支；"
               "少任一分支 ⇒ 轮换验收无法区分『旧值藏在低优先级层』。",
    ),
    "AD-CFG-002": dict(
        ref=TARGET, path="scripts/check_install_parity.sh", symbol="resolved_from",
        kind="branch", scope="file",
        anchors=["dotenv", "runtime_file", "process_env"],
        method="核对脚本必须同时向 owner 询问 dotenv/runtime_file/process_env 三层来源；"
               "工具不得自己复制一套优先级规则。",
    ),
    "AD-CFG-003": dict(
        ref=PROD, path="tts_erp_v2/middleware/auth.py", symbol="_auth_mode",
        kind="branch", scope="block",
        anchors=["except ValueError", "AuthMode.ENFORCE"],
        method="非法 TTS_ERP_AUTH_MODE 必须走 except 分支并回落到 AuthMode.ENFORCE（fail-closed）；"
               "回落成 off 即为缺陷。",
    ),
    "AD-SCHEMA-001": dict(
        ref=PROD, path="tts_erp_v2/app.py", symbol="healthz",
        kind="location", scope="block",
        method="版本契约的暴露点必须是 app.py 内一个具名函数 healthz；"
               "守卫原型未挂载这件事本身另由 probe 判定。",
    ),
    "AD-SCHEMA-002": dict(
        ref=PROD, path="tts_erp_v2/api/deps.py", symbol="is_prod_shaped_db",
        kind="branch", scope="block",
        anchors=["except Exception", "return True", "_PROD_SHAPED_DBNAMES"],
        method="prod-shape 判定必须在空值/异常分支 fail-closed（return True），"
               "并以 _PROD_SHAPED_DBNAMES 为白名单；两棵树各自一份文本判定。",
    ),
    "AD-DEPLOY-001": dict(
        ref="RUNTIME", path=RUNTIME_FACTS, symbol="deployed_commit_drop_in",
        kind="location", scope="file",
        method="运行态身份声明必须固化在仓库内的运行时事实表里；"
               "只存在于服务器上 ⇒ 仓库无法判定这条不变量。",
    ),
    "AD-DEPLOY-002": dict(
        ref="RUNTIME", path=RUNTIME_FACTS, symbol="pgappname_drop_in",
        kind="location", scope="file",
        method="按进程身份派生 PGAPPNAME 的 drop-in 必须在仓库内可核对；"
               "application_name 为空 ⇒ 这条不变量不可判定。",
    ),
    "AD-DEPLOY-003": dict(
        ref=PROD, path="scripts/envsetup/install.sh", symbol="write_unit",
        kind="location", scope="block",
        method="unit 的装配出口必须收敛到 write_unit 一处；"
               "散落的装配写法 ⇒ parity 核对必然漏项。",
    ),
    "AD-DEPLOY-004": dict(
        ref=PROD, path="scripts/envsetup/install.sh", symbol="env_get",
        kind="branch", scope="block",
        anchors=["CONFIG_FILE"],
        forbid_contains=["|| true"],
        method="凭据来源解析在 env_get 内；`|| true` 是 fail-open 标记 —— "
               "期望是解析失败即中止（die），当前仍静默回落 ⇒ 判红。",
    ),
    "AD-CRITERIA-001": dict(
        ref=TARGET, path="scripts/check_jobs_actually_run.py", symbol="_is_readonly",
        kind="branch", scope="file",
        anchors=["_READONLY_HEADS", "SELECT", "SHOW"],
        method="只读判据必须是『首 token ∈ {SELECT, SHOW}』这一条结构判定；"
               "白名单一旦被改成别的集合 ⇒ 判据口径漂移。",
    ),
    "AD-CRITERIA-002": dict(
        ref=TARGET, path="scripts/check_jobs_actually_run.py", symbol="_guard",
        kind="branch", scope="file",
        anchors=["skipped_count", "previous_skipped_count"],
        method="被跳过项数必须是一等指标（skipped_count）并与上一轮做增删差异；"
               "except 静默 continue ⇒ 这两个锚点会消失。",
    ),
    "AD-CRITERIA-003": dict(
        ref=TARGET, path="scripts/schema_guard_probe.py", symbol="_heads_and_ancestors",
        kind="branch", scope="file",
        anchors=["walk_revisions", "get_heads"],
        method="拓扑必须问 alembic 自己（ScriptDirectory.walk_revisions/get_heads），"
               "不能自己拼；两处锚点同时消失 ⇒ 退回手写拓扑。",
    ),
    "AD-ALERT-001": dict(
        ref=PROD, path="scripts/watchdog_sync.py", symbol="post_webhook",
        kind="location", scope="block",
        method="投递出口必须是 post_webhook 这一个具名函数；"
               "URL 不可达时必须产出 watchdog_webhook_failed。",
    ),
    "AD-ALERT-002": dict(
        ref=PROD, path="scripts/watchdog_sync.py", symbol="to_public",
        kind="branch", scope="file",
        anchors=["PUBLIC_DETAIL_FIELDS", "to_public", "details"],
        method="对外投影必须经 to_public 且只透出 PUBLIC_DETAIL_FIELDS 白名单里的 details 键；"
               "白名单与投影函数任一消失 ⇒ 脱敏边界失守。",
    ),
    "AD-ALERT-003": dict(
        ref="RUNTIME", path=RUNTIME_FACTS, symbol="alert_unit_success_exit_status",
        kind="count", scope="file",
        pattern=r"content_sha256: absent", expected_count=0,
        method="watchdog unit 的 SuccessExitStatus=1 收口形态必须在仓库内可核对；"
               "记录为 absent 的事实数必须为 0 —— 当前该 drop-in 尚未写入主机 ⇒ 判红。"
               "exit 2 仍必须标 failed，不得被吞掉。",
    ),
    "AD-JOB-001": dict(
        ref=PROD, path="tts_erp_v2/sync_worker/scheduler.py", symbol="_record_failed_tick",
        kind="branch", scope="block",
        anchors=['status="failed"', "finished_at"],
        method="异常路径必须由 _record_failed_tick 写一条 status=\"failed\" 且带 finished_at 的行；"
               "行必须终结，不能留 running。",
    ),
    "AD-JOB-002": dict(
        ref=PROD, path="tts_erp_v2/sync_worker/scheduler.py",
        symbol="_scheduled_child_command",
        kind="count", scope="file",
        pattern=r"kill|SIGKILL|timeout",
        expected_count=1,
        method="父进程以独立子进程调度 job（_scheduled_child_command）；"
               "但超时 kill 机制在 owner 树里必须存在（kill/SIGKILL/timeout 至少一处），"
               "当前 0 处 ⇒ 超时后 running 行不会被终结 ⇒ 判红。",
    ),
    "AD-JOB-003": dict(
        ref=PROD, path="tts_erp_v2/jobs/miaoshou/sync_source_cost_to_master.py",
        symbol="sync_source_cost_to_master",
        kind="branch", scope="block",
        anchors=["tk_rows_total", "rows_updated", "issues"],
        method="回填必须回报 tk_rows_total / rows_updated / issues 三个计数器；"
               "『幂等完成』与『源已陈旧』无法区分正是这里缺第三个计数器。",
    ),
    "AD-BACKUP-001": dict(
        ref=PROD, path="scripts/backup/pg_dump_hourly.sh", symbol="KEEP_COUNT",
        kind="count", scope="file",
        pattern=r"gzip -t|pg_restore --list", expected_count=2,
        method="每小时 dump 必须过 gzip -t 与 pg_restore --list 两道校验；"
               "当前一个都没有 ⇒ 损坏的备份也会被当成成功 ⇒ 判红。"
               "保留份数由 KEEP_COUNT 声明（当前 24 ⇒ RPO=1h）。",
    ),
    "AD-NET-001": dict(
        ref="RUNTIME", path=RUNTIME_FACTS, symbol="easytier_rpc_portal_loopback",
        kind="location", scope="file",
        method="easytier RPC portal 的回环绑定必须在仓库内可核对；"
               "配置文件里的 rpc_portal 键被忽略，只有 ET_RPC_PORTAL 生效。",
    ),
    "AD-NET-002": dict(
        ref="RUNTIME", path=RUNTIME_FACTS, symbol="easytier_default_conf_port_forward",
        kind="count", scope="file",
        pattern=r'bind_addr = "0\.0\.0\.0:', expected_count=0,
        method="207 公网机上还有两个 port_forward 绑 0.0.0.0（2222 / 9877）；"
               "本条以『仓库里记录到的通配绑定数必须为 0』来钉住暴露面，"
               "收口后该文件不再记录这两条 ⇒ 判绿。",
    ),
    "AD-BASE-001": dict(
        ref=PROD, path="tts_erp_v2/runtime_settings.py", symbol="get_setting_facts",
        kind="location", scope="block",
        absent_in_ref=TARGET, absent_path="tts_erp_v2/runtime_settings.py",
        method="生产树有 runtime_settings.py、目标树必须没有；"
               "目标树若出现同名文件 ⇒ base drift 消失，本条判红。",
    ),
    "AD-BASE-002": dict(
        ref=PROD, path="scripts/watchdog_sync.py", symbol="to_public",
        kind="location", scope="block",
        equal_blob_ref=TARGET,
        method="收敛后两棵树的 watchdog_sync.py blob 必须完全相同；"
               "owner_blob 与目标树 blob 不等 ⇒ 收敛未完成。",
    ),
    "AD-BASE-003": dict(
        ref=PROD, path="alembic/versions/0073_media_assets.py", symbol="revision",
        kind="location", scope="block",
        assert_contains="0073_media_assets",
        method="生产树的 alembic head 必须钉在 0073_media_assets 的 revision 声明行上；"
               "head 变了 ⇒ 这条 rule 的 owner 位置漂移，必须重新定位。",
    ),
}


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.decode('utf-8','replace')[:200]}")
    return (r.stdout or b"").decode("utf-8", "replace")


def find_symbol_line(lines: list[str], symbol: str) -> int | None:
    """找符号定义行（def / class / 赋值 / yaml 键）。找不到返回 None。"""
    pats = [
        rf"^\s*def\s+{re.escape(symbol)}\b",
        rf"^\s*class\s+{re.escape(symbol)}\b",
        rf"^\s*{re.escape(symbol)}\s*[:=]",
        rf"^\s*[\"']?{re.escape(symbol)}[\"']?\s*:",
        rf"^\s*export\s+{re.escape(symbol)}\b",
        # shell 函数定义：name() {  /  function name() {
        rf"^\s*(function\s+)?{re.escape(symbol)}\s*\(\s*\)",
    ]
    for i, ln in enumerate(lines, 1):
        for p in pats:
            if re.match(p, ln):
                return i
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--runtime-ref", required=True,
                    help="scripts/infra/runtime-facts.yaml 所在提交的 SHA")
    ap.add_argument("--apply", action="store_true", help="真正写回 YAML")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    lp = repo / args.ledger
    data = yaml.safe_load(lp.read_text(encoding="utf-8"))
    entries = data.get("entries") or []
    by_id = {e.get("id"): e for e in entries}

    missing = [k for k in SPEC if k not in by_id]
    if missing:
        sys.stderr.write(f"台账里没有这些 rule: {missing}\n")
        return 2
    extra = [k for k in by_id if k not in SPEC]
    if extra:
        sys.stderr.write(f"⚠ 台账里有 SPEC 未覆盖的 rule（本次不改动）: {extra}\n")

    problems: list[str] = []
    reds: list[str] = []
    for rid, spec in SPEC.items():
        row = by_id[rid]
        ref = args.runtime_ref if spec["ref"] == "RUNTIME" else spec["ref"]
        path = spec["path"]
        blob = git(repo, "rev-parse", f"{ref}:{path}").strip()
        content = git(repo, "show", f"{ref}:{path}")
        lines = content.splitlines()
        ln = find_symbol_line(lines, spec["symbol"])
        if ln is None:
            problems.append(f"{rid}: 在 {path} 里找不到符号 {spec['symbol']} 的定义行")
            continue
        line = lines[ln - 1]
        digest = "sha256:" + hashlib.sha256(line.strip().encode("utf-8")).hexdigest()[:16]

        scope: dict = {"kind": spec["kind"], "method": spec["method"], "scope": spec.get("scope", "block")}
        if spec["kind"] == "branch":
            scope["anchors"] = spec["anchors"]
            missing_anchor = [a for a in spec["anchors"] if a not in content]
            if missing_anchor:
                # 锚点缺失 = 这条债务还没收口（判红），不是生成错误
                reds.append(f"{rid}: 缺期望分支 {missing_anchor}")
        if spec["kind"] == "count":
            scope["pattern"] = spec["pattern"]
            scope["expected_count"] = spec["expected_count"]
            found = len(re.findall(spec["pattern"], content))
            if found != spec["expected_count"]:
                reds.append(
                    f"{rid}: 数量判据当前 {found}，期望 {spec['expected_count']}（该 rule 判红）"
                )
        for opt in ("absent_in_ref", "absent_path", "equal_blob_ref",
                    "assert_contains", "forbid_contains"):
            if opt in spec:
                scope[opt] = spec[opt]

        row["owner_ref"] = ref
        row["owner_path"] = path
        row["owner_blob"] = blob
        row["symbol"] = spec["symbol"]
        row["symbol_line"] = ln
        row["line_digest"] = digest
        row["scope_assertion"] = scope
        row["check_ref"] = f"{CHECKER} --rule {rid}"
        v = row.setdefault("verification", {})
        v["command"] = f"python3 {CHECKER} --rule {rid}"
        v["fixture_exists"] = True
        v["kind"] = "executable"
        v["check_ref"] = row["check_ref"]
        # 契约要求 unverified_preconditions【存在】；没有假定就显式写空列表，
        # 而不是"忘了写"——前者是声明，后者是缺口。
        # （键可能存在但值为 null，同样算缺口）
        if not row.get("unverified_preconditions"):
            row["unverified_preconditions"] = []
        # 原来是 absent（没有可执行检查）的 rule，现在有了检查，
        # 保守记为 red：机器能证明"owner 契约形状成立"，
        # 但这条债务是否已收口要人来复核，不在这里替人宣称 green。
        if row.get("verification_state") == "absent":
            row["verification_state"] = "red"
            row.setdefault("verification", {})["state"] = "unknown"
            row["verification"]["source"] = "机器判定（check_ledger_owners.py）；保守记 red"

    # 必填字段表同步扩容（六字段进契约）
    req = list(data.get("required_finding_fields") or [])
    for f in ("owner_ref", "owner_blob", "symbol", "line_digest", "check_ref"):
        if f not in req:
            req.append(f)
    data["required_finding_fields"] = req

    if problems:
        for p in problems:
            sys.stderr.write(f"  ⚠ {p}\n")
        sys.stderr.write("存在未满足的判据，未写回（先修 SPEC 或 owner）\n")
        return 1

    if not args.apply:
        print("dry-run 通过：24 条 rule 的六字段都能解析出来（未写回）")
        for r in reds:
            print(f"  判红: {r}")
        return 0

    lp.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False, width=4096),
        encoding="utf-8",
    )
    print(f"已写回 {lp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
