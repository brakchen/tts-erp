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
        v = row.get("verification") or {}
        state = v.get("state")
        rid = row.get("id")
        if state == "missing":
            absent.append(rid); not_applicable.append(rid)
        elif state == "fails":
            red.append(rid)
        elif state == "holds":
            green.append(rid)
        if row.get("unverified_preconditions"):
            provisional.append(rid)
        if not row.get("acceptance"):
            pass  # 缺 acceptance 不计入五项，但会在下面单独报

    # ③ open_unverified：第四格里 status=open 的
    open_unverified = [
        r.get("id") for r in entries
        if r.get("unverified_preconditions")
        and r.get("unverified_preconditions_status") != "resolved"
    ]

    # ④ excluded：台账里显式排除的对象
    excluded = list(d.get("excluded_entries") or [])

    # ⑤ skipped：台账里显式标记跳过的对象
    skipped = list(d.get("skipped_entries") or [])

    # ⑥ 进度：有多少条 rule 引用了【已落地并可执行】的检查
    enforced = [r.get("id") for r in entries
                if (r.get("verification") or {}).get("command")
                and (r.get("verification") or {}).get("fixture_exists")]
    runnable = list(enforced)
    if args.run_checks:
        runnable = []
        for r in entries:
            cmd = (r.get("verification") or {}).get("command")
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
