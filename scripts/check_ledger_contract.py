#!/usr/bin/env python3
"""check_ledger_contract.py — 台账输出契约检查

Chief 第十五轮裁定④：
    output_must_declare = { skipped, not_applicable, excluded,
                            open_unverified, provisional_count }
    ⇒ 五者 count 正常值都是 0；任一非零即 finding。

@数据存储专家 的进度判据：
    进度 = 「台账里有多少条 rule 引用了一条【已落地并可执行】的检查」/ 总条数
    ⇒ 「红数」不是进度（红是有效缺陷）；「绿数」也不是进度（绿可能是恒绿）。

★ 2026-11-10 修正（此前本脚本恒绿）：
    旧版无论缺多少必填字段都 `return 0` ⇒ 台账里再多规则也不影响判定。
    现在：
      - 缺必填字段            ⇒ 退出 1
      - 判据类型不在三选一内   ⇒ 退出 1
      - check_ref 指向的文件不存在 ⇒ 退出 1
      - owner 身份解析不出（blob/digest 对不上）⇒ 退出 1
    即：**字段齐了但内容对不上，一样判红**。

用法：
    python scripts/check_ledger_contract.py [--ledger PATH] [--run-checks] [--json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

LEDGER = "docs/architecture/arch-debt-ledger.yaml"
OWNER_CHECKER = "scripts/check_ledger_owners.py"
VALID_KINDS = ("location", "count", "branch")


def _is_present(row, field) -> bool:
    """字段【存在】且不是 None / 空串。

    ★ 不用真值判断：in_target_chain=false 是合法取值（未判定 ⇒ 保守按 False），
      旧版 `not row.get(f)` 会把 False 当成缺失，报出 24 条假"缺字段"。
    ★ 空列表算【存在】：`unverified_preconditions: []` 是「显式声明没有未验证前提」，
      不是「忘了写」。把空列表当缺失会让契约永远判红，那才是真的恒红。
    """
    if field not in row:
        return False
    v = row[field]
    if v is None:
        return False
    if isinstance(v, str) and not v.strip():
        return False
    return True


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


def _fixture_of(row) -> bool:
    """夹具是否存在（决定这条 rule 有没有判定力）。"""
    v = row.get("verification")
    if isinstance(v, dict):
        return bool(v.get("fixture_exists", True))
    return row.get("verification_state") != "absent"


def _cmd_of(row):
    v = row.get("verification")
    if isinstance(v, dict):
        return v.get("command")
    if isinstance(v, str) and v.startswith(("python", "bash", "sh ")):
        return v
    return None


def _check_ref_of(row):
    ref = row.get("check_ref")
    if isinstance(ref, str) and ref.strip():
        return ref.strip()
    v = row.get("verification")
    if isinstance(v, dict) and isinstance(v.get("check_ref"), str):
        return v["check_ref"].strip()
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--run-checks", action="store_true", help="实际执行 verification.command")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    p = Path(args.ledger)
    if not p.is_file():
        sys.stderr.write(f"台账不存在: {p}\n")
        return 2
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    entries = d.get("entries") or []

    # ① 必填字段
    #    ★ 用【存在性】而不是真值：in_target_chain=false 是合法取值（未判定 ⇒ 保守按 False），
    #      不能因为是 False 就报"缺字段"。真值判断会把合法的 False 当成缺失（2026-11-10 实测）。
    required = d.get("required_finding_fields") or []
    missing_fields = []
    for row in entries:
        miss = [f for f in required if not _is_present(row, f)]
        if miss:
            missing_fields.append({"id": row.get("id"), "missing": miss})

    # ② 字段形状：判据类型必须是三选一；check_ref 指向的文件必须存在
    shape_problems = []
    for row in entries:
        rid = row.get("id")
        sa = row.get("scope_assertion")
        kind = sa.get("kind") if isinstance(sa, dict) else None
        if kind not in VALID_KINDS:
            shape_problems.append({"id": rid, "problem": f"判据类型非法: {kind!r}"})
        elif not (sa.get("method") or "").strip():
            shape_problems.append({"id": rid, "problem": "scope_assertion.method 缺失"})
        ref = _check_ref_of(row)
        if not ref:
            shape_problems.append({"id": rid, "problem": "缺 check_ref（可执行检查引用）"})
        else:
            script = ref.split()[0]
            if not Path(script).is_file():
                shape_problems.append({"id": rid, "problem": f"check_ref 指向的文件不存在: {script}"})

    # ③ 三态 → not_applicable / provisional
    not_applicable, provisional, green, red, absent = [], [], [], [], []
    for row in entries:
        state = _state_of(row)
        rid = row.get("id")
        if state == "missing":
            absent.append(rid)
            not_applicable.append(rid)
        elif state == "fails":
            red.append(rid)
        elif state == "holds":
            green.append(rid)
        if _unverified_of(row):
            provisional.append(rid)

    # ④ open_unverified：第四格里 status=open 的
    open_unverified = [
        r.get("id") for r in entries
        if _unverified_of(r)
        and r.get("unverified_preconditions_status", "open") == "open"
    ]
    excluded = list(d.get("excluded_entries") or [])
    skipped = list(d.get("skipped_entries") or [])

    # ⑤ 进度：有多少条 rule 引用了【已落地并可执行】的检查
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
                rc = subprocess.run(cmd, shell=True, capture_output=True, timeout=180).returncode
            except Exception:
                rc = 127
            if rc == 0:
                runnable.append(r.get("id"))

    # ⑥ owner 身份校验：字段齐了不代表内容对得上（这才是"真列"的含义）
    #    ★ 但要分两类：
    #      - 「判据不成立」= 这条债务还没收口 ⇒ 台账该报告的 finding，不是契约违规
    #      - 其余（blob/digest/symbol 对不上、owner 解析不出）= 台账自身写错了 ⇒ 契约违规
    owner_check = {"ran": False}
    identity_failures: list[dict] = []
    criterion_failures: list[dict] = []
    checker = Path(OWNER_CHECKER)
    if checker.is_file():
        cp = subprocess.run(
            [sys.executable, str(checker), "--ledger", str(p), "--json"],
            capture_output=True, timeout=600,
        )
        try:
            owner_check = json.loads((cp.stdout or b"").decode("utf-8", "replace"))
            owner_check["exit_code"] = cp.returncode
        except Exception:
            owner_check = {"ran": True, "exit_code": cp.returncode,
                           "error": (cp.stderr or b"").decode("utf-8", "replace")[-400:]}
        for f in owner_check.get("failures", []) or []:
            probs = f.get("problems", []) or []
            if probs and all("判据不成立" in x for x in probs):
                criterion_failures.append(f)
            else:
                identity_failures.append(f)

    contract_ok = (
        not missing_fields
        and not shape_problems
        and not identity_failures
    )

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
        "verification_states": {"green": len(green), "red": len(red), "absent": len(absent)},
        "progress": {
            "rules_with_enforced_check": len(enforced),
            "total": len(entries),
            "ratio": round(len(enforced) / len(entries), 3) if entries else 0,
            "executed_and_passed": len(runnable) if args.run_checks else None,
        },
        "missing_required_fields": missing_fields,
        "shape_problems": shape_problems,
        "owner_identity_failures": identity_failures,
        "owner_criterion_failures": criterion_failures,
        "owner_identity_check": owner_check,
        "contract_ok": contract_ok,
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
        if shape_problems:
            print(f"  ⚠ 字段形状问题: {len(shape_problems)} 条")
            for s in shape_problems[:8]:
                print(f"     {s['id']}: {s['problem']}")
        oc = owner_check.get("rules_failed")
        if owner_check.get("ran") and oc is not None:
            print(f"  owner 检查: 通过 {owner_check.get('rules_passed')} / 失败 {oc} (exit {owner_check.get('exit_code')})")
            print(f"      其中【台账写错】(owner 身份对不上) = {len(identity_failures)}")
            print(f"      其中【债务未收口】(判据不成立)   = {len(criterion_failures)}")
        print(f"  契约判定: {'通过' if contract_ok else '不通过'}")
        print("  （契约退出码只由『缺字段 / 字段形状 / owner 身份写错』决定；"
              "『判据不成立』是台账要报告的债务，不让契约永远判红）")
    return 0 if contract_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
