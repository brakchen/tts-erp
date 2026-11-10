#!/usr/bin/env python3
"""check_ledger_owners.py — 台账 owner 身份与判据的机器检查

Chief 2026-11-10 的要求：
    台账的每条 rule 必须有【真列】：
        owner_ref + owner_blob + symbol + line_digest
        + 可执行检查引用 + 判据类型
    「JSONB 无 schema」⇒ 检查器**不能靠字段名形状**判断有效性，
    必须回到 git 对象上解析。

本脚本做什么：
    ① owner 身份：git rev-parse <owner_ref>:<owner_path> 必须等于 owner_blob；
       该 blob 的第 symbol_line 行去空白后的 sha256 前 16 位必须等于 line_digest；
       该行必须真的含 symbol（⇒ owner 不是"随便写个名字"）。
    ② 判据类型三类，**各自的 method 分开**：
         location  位置       —— 符号定义行必须就是被钉住的那一行
         count     数量枚举   —— 模式在 owner blob 里的出现次数必须等于声明值
         branch    分支覆盖   —— 声明的分支锚点必须全部出现在被检查的范围内
    ③ 可执行检查引用：check_ref 指向的文件必须存在；
       verification.command 引用的脚本必须存在。

退出码：
    0  全部规则通过
    1  有规则不满足（缺字段 / owner 解析不出 / 判据不成立 / 检查引用缺失）
    2  用法错误（台账不存在等）

为什么不是"恒绿"：
    「字段在不在」和「字段对不对」是两件事。
    字段齐了但 owner_blob 对不上 ⇒ 一样判红。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

LEDGER_DEFAULT = "docs/architecture/arch-debt-ledger.yaml"
KINDS = ("location", "count", "branch")

# 六字段：缺任何一条 ⇒ 判红
SIX_FIELDS = (
    "owner_ref",
    "owner_blob",
    "symbol",
    "line_digest",
    "check_ref",
    "scope_assertion",
)


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, timeout=120
        )
    except Exception as exc:  # noqa: BLE001
        return 127, str(exc)
    return r.returncode, (r.stdout or b"").decode("utf-8", "replace")


def _blob(repo: Path, ref: str, path: str) -> tuple[str | None, str]:
    """解析 <ref>:<path> 的对象 id（文件=blob，目录=tree）。"""
    rc, out = _git(repo, "rev-parse", f"{ref}:{path}")
    if rc != 0:
        return None, "ref/path 解析失败"
    return out.strip(), ""


def _content(repo: Path, ref: str, path: str) -> tuple[str | None, str]:
    rc, out = _git(repo, "show", f"{ref}:{path}")
    if rc != 0:
        return None, "无法读取 blob 内容"
    return out, ""


def _line_digest(line: str) -> str:
    return "sha256:" + hashlib.sha256(line.strip().encode("utf-8")).hexdigest()[:16]


def _block(lines: list[str], start_idx: int) -> str:
    """从符号定义行起，取到下一个同级 def/class 之前的内容。"""
    indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
    out = [lines[start_idx]]
    for ln in lines[start_idx + 1:]:
        if not ln.strip():
            out.append(ln)
            continue
        cur = len(ln) - len(ln.lstrip())
        if cur <= indent and re.match(r"\s*(def |class |@)", ln):
            break
        out.append(ln)
    return "\n".join(out)


def _check_entry(repo: Path, row: dict[str, Any]) -> dict[str, Any]:
    """返回 {id, ok, problems: [...]}。"""
    rid = row.get("id")
    problems: list[str] = []

    # ① 六字段在不在
    for f in SIX_FIELDS:
        if not row.get(f):
            problems.append(f"缺字段 {f}")
    if problems:
        return {"id": rid, "ok": False, "problems": problems}

    ref = str(row["owner_ref"])
    path = str(row.get("owner_path") or "")
    symbol = str(row["symbol"])
    scope = row.get("scope_assertion") or {}
    kind = scope.get("kind")

    # ② 判据类型必须是三选一，且 method 必须存在
    if kind not in KINDS:
        problems.append(f"判据类型非法: {kind!r}（应为 {KINDS} 之一）")
    if not scope.get("method"):
        problems.append("scope_assertion.method 缺失")

    # ③ owner 身份：blob 必须真的等于 <ref>:<path> 的对象 id
    got, err = _blob(repo, ref, path)
    if got is None:
        problems.append(f"owner 解析不出: {ref}:{path} ({err})")
    elif got != str(row["owner_blob"]):
        problems.append(f"owner_blob 对不上: 台账 {row['owner_blob']} vs 实际 {got}")

    # ④ symbol 行与 line_digest
    content, err = _content(repo, ref, path)
    if content is None:
        problems.append(f"owner 内容读不出: {ref}:{path} ({err})")
        return {"id": rid, "ok": False, "problems": problems}

    lines = content.splitlines()
    ln = row.get("symbol_line")
    if not isinstance(ln, int) or ln < 1 or ln > len(lines):
        problems.append(f"symbol_line 越界或缺失: {ln}")
        return {"id": rid, "ok": False, "problems": problems}

    line = lines[ln - 1]
    digest = _line_digest(line)
    if digest != str(row["line_digest"]):
        problems.append(f"line_digest 对不上: 台账 {row['line_digest']} vs 实际 {digest}")
    if not re.search(rf"\b{re.escape(symbol)}\b", line):
        problems.append(f"第 {ln} 行不含 symbol {symbol!r}（钉住的不是符号定义行）")

    # ⑤ 判据本体：三类各自的方法
    scope_kind = scope.get("scope", "block")
    target = _block(lines, ln - 1) if scope_kind == "block" else content

    if kind == "location":
        if not re.match(rf"\s*(def |class |{re.escape(symbol)}\s*[:=]|export )", line) \
                and not re.match(rf"\s*[\"']?{re.escape(symbol)}[\"']?\s*:", line) \
                and not re.match(rf"\s*(function\s+)?{re.escape(symbol)}\s*\(\s*\)", line):
            problems.append(f"location 判据不成立：第 {ln} 行不是符号定义行")
        # 额外断言：被钉住的那一行还必须含某个字面量（例如 revision 声明的值）
        need = scope.get("assert_contains")
        if need and str(need) not in line:
            problems.append(
                f"location 判据不成立：第 {ln} 行不含要求的字面量 {need!r}"
            )
        # 额外断言：被钉住的那一行必须【不含】某个字面量（fail-open 标记等）
        forbid = scope.get("forbid_contains")
        if forbid:
            hit = [f for f in (forbid if isinstance(forbid, list) else [forbid]) if str(f) in line]
            if hit:
                problems.append(f"location 判据不成立：第 {ln} 行仍含被禁止的字面量 {hit}")
        # 可选：同一路径在另一 ref 必须【不存在】（base-drift 的『缺席』断言）
        absent_ref = scope.get("absent_in_ref")
        if absent_ref:
            absent_path = scope.get("absent_path", path)
            got2, _ = _blob(repo, str(absent_ref), str(absent_path))
            if got2 is not None:
                problems.append(
                    f"location 判据不成立：{absent_ref}:{absent_path} 本应不存在，实际存在（{got2}）"
                )
        # 可选：另一 ref 的同一路径必须 blob 相同（收敛断言）
        equal_ref = scope.get("equal_blob_ref")
        if equal_ref:
            got3, err3 = _blob(repo, str(equal_ref), path)
            if got3 is None:
                problems.append(f"equal_blob_ref 解析不出: {equal_ref}:{path} ({err3})")
            elif got3 != str(row["owner_blob"]):
                problems.append(
                    f"location 判据不成立：{equal_ref} 的 blob {got3} 与 owner {row['owner_blob']} 不相等"
                )
    elif kind == "count":
        pattern = scope.get("pattern")
        expected = scope.get("expected_count")
        minimum = scope.get("min_count")
        maximum = scope.get("max_count")
        if not pattern:
            problems.append("count 判据缺 pattern")
        elif expected is None and minimum is None and maximum is None:
            problems.append("count 判据缺 expected_count / min_count / max_count")
        else:
            found = len(re.findall(str(pattern), target))
            # ★ expected_count 是【恰好】；min_count/max_count 是【区间】。
            #   写「恰好 1」而修复后出现 3 处 ⇒ 修好了仍然判红 ⇒ 判据本身是坏的。
            #   「超时 kill 机制必须存在」这类需求要用 min_count。
            if isinstance(expected, int) and found != expected:
                problems.append(
                    f"count 判据不成立：模式 {pattern!r} 实际命中 {found} 次，声明恰好 {expected} 次"
                )
            if isinstance(minimum, int) and found < minimum:
                problems.append(
                    f"count 判据不成立：模式 {pattern!r} 实际命中 {found} 次，少于下限 {minimum} 次"
                )
            if isinstance(maximum, int) and found > maximum:
                problems.append(
                    f"count 判据不成立：模式 {pattern!r} 实际命中 {found} 次，多于上限 {maximum} 次"
                )
    elif kind == "branch":
        anchors = scope.get("anchors")
        if not isinstance(anchors, list) or not anchors:
            problems.append("branch 判据缺 anchors")
        else:
            missing = [a for a in anchors if str(a) not in target]
            if missing:
                problems.append(f"branch 判据不成立：缺分支锚点 {missing}")
        forbid = scope.get("forbid_contains")
        if forbid:
            hit = [f for f in (forbid if isinstance(forbid, list) else [forbid]) if str(f) in target]
            if hit:
                problems.append(f"branch 判据不成立：仍含被禁止的分支 {hit}")

    # ⑥ 可执行检查引用必须真的存在
    check_ref = str(row.get("check_ref") or "")
    if check_ref:
        script = check_ref.split()[0]
        if not (repo / script).is_file():
            problems.append(f"check_ref 指向的文件不存在: {script}")
    cmd = (row.get("verification") or {}).get("command")
    if not cmd:
        problems.append("verification.command 缺失（该 rule 没有可执行检查引用）")
    else:
        m = re.search(r"(?:python3?|bash|sh)\s+([\w./-]+\.(?:py|sh))", str(cmd))
        if m and not (repo / m.group(1)).is_file():
            problems.append(f"verification.command 引用的脚本不存在: {m.group(1)}")

    return {"id": rid, "ok": not problems, "problems": problems}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", default=LEDGER_DEFAULT)
    ap.add_argument("--repo", default=".", help="用于解析 owner 的仓库")
    ap.add_argument("--rule", action="append", default=None, help="只检查指定 rule id（可多次）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    lp = Path(args.ledger)
    if not lp.is_file():
        sys.stderr.write(f"台账不存在: {lp}\n")
        return 2
    try:
        import yaml
    except ImportError:
        sys.stderr.write("需要 PyYAML\n")
        return 2
    ledger = yaml.safe_load(lp.read_text(encoding="utf-8")) or {}
    entries = ledger.get("entries") or []
    repo = Path(args.repo).resolve()

    if args.rule:
        wanted = set(args.rule)
        selected = [r for r in entries if r.get("id") in wanted]
        missing = wanted - {r.get("id") for r in entries}
        if missing:
            sys.stderr.write(f"台账里没有这些 rule id: {sorted(missing)}\n")
            return 2
    else:
        selected = entries

    results = [_check_entry(repo, r) for r in selected]
    failed = [r for r in results if not r["ok"]]
    passed = [r for r in results if r["ok"]]

    report = {
        "ledger": str(lp),
        "repo": str(repo),
        "rules_checked": len(results),
        "rules_passed": len(passed),
        "rules_failed": len(failed),
        "failures": failed,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"台账 owner/判据检查 — {lp}")
        print(f"  检查规则数 = {len(results)}")
        print(f"  通过 = {len(passed)}   失败 = {len(failed)}")
        for f in failed:
            print(f"  ⚠ {f['id']}")
            for p in f["problems"]:
                print(f"      - {p}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
