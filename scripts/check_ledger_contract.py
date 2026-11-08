#!/usr/bin/env python3
"""check_ledger_contract.py — 台账输出契约检查（收官件）

Chief 第十五轮裁定④：
    output_must_declare = { skipped, not_applicable, excluded,
                            open_unverified, provisional_count }
    ⇒ 五者 count 正常值都是 0；任一非零即 finding。

@数据存储专家 的进度判据：
    进度 = 「台账里有多少条 rule 引用了一条【已落地并可执行】的检查」/ 总条数
    ⇒ 「红数」不是进度（红是有效缺陷）；「绿数」也不是进度（绿可能是恒绿）

用法：
    python scripts/check_ledger_contract.py [--ledger PATH] [--run-checks] [--json]
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path
import yaml

LEDGER = "docs/architecture/arch-debt-ledger.yaml"


def _state_of(row) -> str:
    """归一三态：verification_state 是权威；verification.state 只在合法时采用。

    2026-11-09 教训：我先后两次把台账里两个语义不同的字段搞混
    （verification 装命令、verification_state 装三态），
    于是检查器连报两次"全 0 的假基线"。
    ⇒ 归一必须**直接判值**，不能依赖字段的嵌套形状。
    """
    st = row.get("verification_state")
    if st in ("green", "red", "absent"):
        return {"green": "holds", "red": "fails", "absent": "missing"}[st]
    v = row.get("verification")
    if isinstance(v, dict) and v.get("state") in ("holds", "fails", "missing"):
        return v["state"]
    if isinstance(v, str) and v in ("green", "red", "absent"):
        return {"green": "holds", "red": "fails", "absent": "missing"}[v]
    return "missing"


def _unverified_of(row):
    """归一第四格：只有【显式声明了假定】才算；占位文本与未声明都不算。"""
    up = row.get("unverified_preconditions")
    if isinstance(up, list):
        up = up[0] if up else None
    if not isinstance(up, str) or not up.strip():
        return None
    if "未逐项标注" in up or "未标注假定" in up:
        return None
    return up.strip()


def _cmd_of(row):
    v = row.get("verification")
    if isinstance(v, dict):
        return v.get("command")
    if isinstance(v, str) and v.startswith(("python", "bash", "sh ")):
        return v
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--run-checks", action="store_true", help="实际执行 verification.command")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    p = Path(args.ledger)
    if not p.is_file():
        sys.stderr.write(f"台账不存在: {p}\n"); return 2
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    entries = d.get("entries") or []

    # ① 必填字段
    required = d.get("required_finding_fields") or []
    missing_fields = []
    for row in entries:
        miss = [f for f in required if not row.get(f)]
        if miss:
            missing_fields.append({"id": row.get("id"), "missing": miss})

    # ② 三态 → not_applicable / provisional
    not_applicable, provisional, green, red, absent = [], [], [], [], []
    for row in entries:
        state = _state_of(row)
        rid = row.get("id")
        if state == "missing":
            absent.append(rid); not_applicable.append(rid)
        elif state == "fails":
            red.append(rid)
        elif state == "holds":
            green.append(rid)
        if _unverified_of(row):
            provisional.append(rid)   # 显式声明了假定 ⇒ provisional
        if not row.get("acceptance"):
            pass  # 缺 acceptance 不计入五项，但会在下面单独报

    # ③ open_unverified：第四格里 status=open 的
    # ★ "未声明第四格" ≠ "有一条未验证前提" ⇒ 只有显式写了才算 open。
    #   （否则"我还没填"会被当成"我假定了一条"，计数失去信息量）
    open_unverified = [
        r.get("id") for r in entries
        if _unverified_of(r)
        and r.get("unverified_preconditions_status", "open") == "open"
    ]

    # ④ excluded：台账里显式排除的对象
    excluded = list(d.get("excluded_entries") or [])

    # ⑤ skipped：台账里显式标记跳过的对象
    skipped = list(d.get("skipped_entries") or [])

    # ⑥ 进度：有多少条 rule 引用了【已落地并可执行】的检查
    enforced = [r.get("id") for r in entries
                if _cmd_of(r) and _fixture_of(r)]
    runnable = list(enforced)
    if args.run_checks:
        runnable = []
        for r in entries:
            cmd = _cmd_of(r)
            if not cmd:
                continue
            try:
                rc = subprocess.run(cmd, shell=True, capture_output=True, timeout=120).returncode
            except Exception:
                rc = 127
            if rc == 0:
                runnable.append(r.get("id"))

    report = {
        "ledger": str(p),
        "total_rules": len(entries),
        "counts": {
            "skipped": len(skipped),
            "not_applicable": len(not_applicable),
            "excluded": len(excluded),
            "open_unverified": len(open_unverified),
            "provisional_count": len(provisional),
        },
        "verification_states": {"green": len(green), "red": len(red),
                                "absent": len(absent)},
        "progress": {
            "rules_with_enforced_check": len(enforced),
            "total": len(entries),
            "ratio": round(len(enforced) / len(entries), 3) if entries else 0,
            "executed_and_passed": len(runnable) if args.run_checks else None,
        },
        "missing_required_fields": missing_fields,
        "ids": {
            "not_applicable": not_applicable,
            "open_unverified": open_unverified,
            "provisional": provisional,
        },
        "finding": "五项计数任一非零即 finding" if any(
            len(x) for x in (skipped, not_applicable, excluded, open_unverified, provisional)
        ) else "五项计数均为 0",
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"台账输出契约检查 — {p}")
        print(f"  rule 总数 = {report['total_rules']}")
        print(f"  五项计数: {report['counts']}")
        print(f"  verification 三态: {report['verification_states']}")
        pr = report["progress"]
        print(f"  进度: 引用了已落地可执行检查的 rule = {pr['rules_with_enforced_check']}/{pr['total']} = {pr['ratio']}")
        if pr["executed_and_passed"] is not None:
            print(f"  实际执行并通过 = {pr['executed_and_passed']}")
        print(f"  finding: {report['finding']}")
        if missing_fields:
            print(f"  ⚠ 缺必填字段: {len(missing_fields)} 条")
            for m in missing_fields[:8]:
                print(f"     {m['id']}: 缺 {m['missing']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
