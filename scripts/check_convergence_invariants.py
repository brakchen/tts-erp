#!/usr/bin/env python3
"""check_convergence_invariants.py — 收敛不变量检查（阶段 0，只读）

Chief 第十轮裁定：
  **收敛不变量 =「每条 rule 在同一 DB 上跑出来的判定结果必须一致」。**
  「写法不变」只是必要条件。

本脚本的做法：
  ① 读台账 docs/architecture/arch-debt-ledger.yaml
  ② 对每条带 probe 的规则：
     - 用 `git archive <ref>` 把 owner 树导出到临时目录（**只读，不动工作区**）
     - 在各自目录里分两个【独立子进程】跑 probe（各自 PYTHONPATH，互不遮蔽）
     - 比对两边的 JSON 输出
  ③ **任一侧跑不起来 ⇒ finding（unresolved），不是「通过」**
     （属性③：跳过显形；"跑不起来"与"判定一致"必须可区分）

★ 一条必须内建的安全纪律：**probe 的输出会被写进日志/工单** ⇒
  probe 源码里禁止出现凭据；本脚本额外对 probe 的 stdout 做**兜底脱敏**
  （形如 scheme://user:pass@host 的片段一律打码），避免探针自己成为泄露面。

用法：
    python scripts/check_convergence_invariants.py [--ledger PATH] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

LEDGER_DEFAULT = "docs/architecture/arch-debt-ledger.yaml"
# 兜底脱敏：URL 里的 user:password@host
_CRED_RE = re.compile(r"://([^/@\s:]+):([^/@\s]+)@")


def _redact(text: str) -> str:
    """把 URL 里的 user:password@ 段打码（探针输出的兜底防护）。"""
    return _CRED_RE.sub(r"://\1:***@", text)


def _load_ledger(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        sys.stderr.write("需要 PyYAML 才能读台账\n")
        raise SystemExit(2)
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _export_tree(repo: Path, ref: str, dest: Path) -> bool:
    """用 git archive 把某个 ref 的整棵树导出到 dest（只读）。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar", ref],
            capture_output=True, timeout=180,
        )
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"git archive {ref} failed: {exc}\n")
        return False
    if r.returncode != 0:
        sys.stderr.write(
            f"git archive {ref} failed: {_redact(r.stderr.decode('utf-8', 'replace'))[:200]}\n")
        return False
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
        tf.extractall(dest, filter="data")
    return True


def _run_probe(tree_dir: Path, probe_src: str, timeout: int = 60) -> tuple[Any, str]:
    """在导出树里跑 probe，返回 (解析后的 JSON, 错误信息)。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tree_dir) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        r = subprocess.run(
            [sys.executable, "-c", probe_src],
            cwd=str(tree_dir), env=env, capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, "probe timeout"
    except Exception as exc:  # noqa: BLE001
        return None, f"probe launch failed: {exc}"
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    err = (r.stderr or b"").decode("utf-8", "replace").strip()
    if r.returncode != 0:
        return None, _redact(err)[-400:]
    if not out:
        return None, "probe produced no output"
    try:
        return json.loads(out.splitlines()[-1]), ""
    except Exception:  # noqa: BLE001
        return None, f"probe output is not JSON: {_redact(out)[-200:]}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", default=LEDGER_DEFAULT)
    ap.add_argument("--repo", default=".", help="用于 git archive 的仓库")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    ledger_path = Path(args.ledger)
    if not ledger_path.is_file():
        sys.stderr.write(f"台账不存在: {ledger_path}\n")
        return 2
    ledger = _load_ledger(ledger_path)
    entries = ledger.get("entries") or []
    repo = Path(args.repo).resolve()
    base = ledger.get("base") or {}
    refs = {
        "production": base.get("production_ref"),
        "target": base.get("target_ref"),
    }

    findings: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    checked = 0

    with tempfile.TemporaryDirectory(prefix="conv-inv-") as tmp:
        tmpdir = Path(tmp)
        trees: dict[str, Path] = {}
        for key, ref in refs.items():
            if not ref:
                continue
            dest = tmpdir / key
            dest.mkdir(parents=True, exist_ok=True)
            if _export_tree(repo, ref, dest):
                trees[key] = dest
            else:
                unresolved.append({"rule_ref": key, "reason": f"cannot export {ref}"})

        for row in entries:
            probe = (row.get("probe") or {}).get("python")
            if not probe:
                continue
            checked += 1
            rid = row.get("id")
            rule = row.get("rule")
            results: dict[str, Any] = {}
            for key, tree in trees.items():
                val, err = _run_probe(tree, probe)
                if err:
                    unresolved.append({"id": rid, "rule": rule, "ref": key, "reason": err})
                else:
                    results[key] = val
            if len(results) == 2:
                a, b = results.get("production"), results.get("target")
                if a != b:
                    findings.append({
                        "id": rid, "rule": rule,
                        "kind": "behaviour_differs",
                        "production": a, "target": b,
                        "note": "收敛后同一输入下判定结果不一致 ⇒ 收敛会引入规则漂移",
                    })
            elif len(results) == 1:
                unresolved.append({
                    "id": rid, "rule": rule,
                    "reason": f"only one side resolved: {list(results)}",
                })

    # ★ 正对照（零依赖、永远可跑）：把【同一个 ref】导出两次并比对。
    #   若同一来源两次判定不同 ⇒ 检查器本身坏了。
    #   没有正对照，"恒报差异"与"恒报一致"在输出上不可区分（属性②缺口）。
    self_check: dict[str, Any] = {"ran": False}
    probe_rows = [r for r in entries if (r.get("probe") or {}).get("python")]
    if probe_rows and refs.get("production"):
        with tempfile.TemporaryDirectory(prefix="conv-self-") as tmp2:
            t2 = Path(tmp2)
            outs = []
            for k in ("a", "b"):
                dest = t2 / k
                dest.mkdir(parents=True, exist_ok=True)
                if _export_tree(repo, refs["production"], dest):
                    val, err = _run_probe(dest, probe_rows[0]["probe"]["python"])
                    outs.append((val, err))
                else:
                    outs.append((None, "export failed"))
            if outs[0][0] is not None and outs[1][0] is not None:
                self_check = {
                    "ran": True,
                    "rule": probe_rows[0].get("id"),
                    "same": outs[0][0] == outs[1][0],
                    "detail_a": outs[0][0],
                    "detail_b": outs[1][0],
                }
            else:
                self_check = {"ran": True, "rule": probe_rows[0].get("id"),
                              "same": False, "error": outs[0][1] or outs[1][1]}

    report = {
        "ledger": str(ledger_path),
        "self_check": self_check,
        "refs": refs,
        "rules_checked": checked,
        "behaviour_findings": findings,
        "unresolved": unresolved,
        "note": "「unresolved」不是「通过」——跑不起来与判定一致必须可区分",
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"收敛不变量检查 — 台账 {ledger_path}")
        print(f"  production_ref = {refs.get('production')}")
        print(f"  target_ref     = {refs.get('target')}")
        print(f"  规则数（带 probe）= {checked}")
        sc = report.get("self_check") or {}
        if sc.get("ran"):
            print(f"  自检（同一来源比两次）: rule={sc.get('rule')} same={sc.get('same')}")
        print()
        print(f"行为差异（收敛会引入规则漂移）: {len(findings)}")
        for f in findings:
            print(f"  ⚠ {f['id']} [{f['rule']}]")
            print(f"      production = {f['production']}")
            print(f"      target     = {f['target']}")
        print()
        print(f"未能判定（一等指标，正常值 0）: {len(unresolved)}")
        for u in unresolved:
            print(f"  ⚠ {u.get('id', u.get('rule_ref'))}: {u.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
