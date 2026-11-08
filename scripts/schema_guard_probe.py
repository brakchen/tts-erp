#!/usr/bin/env python3
"""schema_guard_probe.py — 启动守卫原型的【只读】验证。

⚠️ 本脚本【不挂载到任何启动路径】。它只做一次判定演算，回答一个问题：
   如果现在给应用加上"库版本 ∉ 本树 revisions ⇒ 拒绝启动"的守卫，
   它会对当前各棵树给出什么结论？

判据（两条，来自 @数据存储专家 的规格）：
   ① applied ∉ local_revisions      —— 本树从没见过这个版本
   ② applied 不是 local head 的祖先   —— 库比代码树旧，读侧会缺列

祖先判定必须用 alembic 自己的 revision map（ScriptDirectory），
不要手写 revision 图。

用法：
    python scripts/schema_guard_probe.py --tree <路径> [--tree <路径> ...]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


def _local_revisions(tree: Path) -> dict[str, str]:
    """本树 alembic/versions/*.py 的 revision id → 文件名。

    2026-11-08 实测教训：用正则扫文件只能命中少数几种书写形式
    （`revision = "x"` / `revision: str = "x"` / 缩进不同）——
    75 个 migration 只命中 6 个。改用 alembic 自己的 ScriptDirectory。
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(tree / "alembic"))
    cfg.set_main_option("version_locations", str(tree / "alembic" / "versions"))
    try:
        script = ScriptDirectory.from_config(cfg)
        return {rev.revision: (getattr(rev, "path", "") or "") for rev in script.walk_revisions()}
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"ScriptDirectory failed for {tree}: {exc}\n")
        return {}


def _db_version() -> str | None:
    """读生产库 alembic_version（只读）。"""
    try:
        from sqlalchemy import text

        from tts_erp_v2.db.base import get_session_factory

        factory = get_session_factory()
        session = factory()
        try:
            rows = session.execute(text("SELECT version_num FROM alembic_version")).all()
        finally:
            session.close()
        return rows[0][0] if rows else None
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"cannot read alembic_version: {exc}\n")
        return None


def _heads_and_ancestors(tree: Path) -> tuple[list[str], set[str]]:
    """向 alembic【问】heads 与祖先集合。

    2026-11-09 修正（通则 22：验证工具不得复制被验证的规则）：
      原实现自己按 `down_revision` 走链 ⇒ 那是在**复制** alembic 的祖先判定，
      而 alembic 的 revision 图有 tuple 分叉，自己走链必然写不对。
      ⇒ 现在改为问 alembic 自己的拓扑序：`ScriptDirectory.walk_revisions()`
      无参调用返回 base→heads 的全部 revision（alembic 内部处理分叉/合并）。
      ⇒ 「某 revision 是某 head 的祖先」= 它在拓扑序里位于那个 head 之前。
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(tree / "alembic"))
    cfg.set_main_option("version_locations", str(tree / "alembic" / "versions"))
    try:
        script = ScriptDirectory.from_config(cfg)
        heads = list(script.get_heads())
        # ↓↓↓ 问 alembic 的拓扑序，不再自己走 down_revision 链 ↓↓↓
        order = [rev.revision for rev in script.walk_revisions()]
        pos = {rid: i for i, rid in enumerate(order)}
        ancestors: set[str] = set()
        for head in heads:
            hi = pos.get(head)
            if hi is None:
                continue
            for rid, i in pos.items():
                if i < hi:
                    ancestors.add(rid)
        return heads, ancestors
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"alembic ScriptDirectory failed for {tree}: {exc}\n")
        return [], set()


def _down_map(tree: Path) -> dict[str, Any]:
    """revision id -> down_revision（可能��� tuple）。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(tree / "alembic"))
    cfg.set_main_option("version_locations", str(tree / "alembic" / "versions"))
    try:
        script = ScriptDirectory.from_config(cfg)
        return {r.revision: r.down_revision for r in script.walk_revisions()}
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"ScriptDirectory failed for {tree}: {exc}\n")
        return {}


def _descendants(tree: Path, applied: str | None) -> list[str]:
    """applied 之后的所有 revision（按 down 链往下走，含 tuple 分支）。"""
    down = _down_map(tree)
    children: dict[str, list[str]] = {}
    for rev, d in down.items():
        if isinstance(d, (list, tuple)):
            for n in d:
                children.setdefault(n, []).append(rev)
        elif d:
            children.setdefault(d, []).append(rev)
    out: list[str] = []
    stack = [applied] if applied else []
    seen: set[str] = set()
    while stack:
        cur = stack.pop()
        if not cur or cur in seen:
            continue
        seen.add(cur)
        out.append(cur)
        stack.extend(children.get(cur, []))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tree", action="append", required=True, help="要检查的代码树（可多次）")
    ap.add_argument(
        "--other-tree", action="append", default=[],
        help="参照树（用于计算 missing_revisions：参照树有、本树缺的 revision）",
    )
    ap.add_argument(
        "--mode", choices=("observe", "enforce"), default="observe",
        help="observe = 只报告不拒绝（默认）；enforce = 不匹配则拒绝启动",
    )
    ap.add_argument("--json", action="store_true", help="输出 JSON finding")
    args = ap.parse_args(argv)

    applied = _db_version()
    if not args.json:
        print(f"生产库 alembic_version = {applied}")
        print(f"守卫档位 = {args.mode}（observe 不拒绝启动）")
        print()

    findings = []
    for raw in args.tree:
        tree = Path(raw).resolve()
        local = _local_revisions(tree)
        heads, ancestors = _heads_and_ancestors(tree)
        unknown = applied is not None and applied not in local
        not_ancestor = applied is not None and applied not in ancestors
        compatible = not (unknown or not_ancestor)

        # missing_revisions：参照树里【本树缺的】revision。
        # 口径 = (参照树 head 祖先链上的 revision) − (本树 revision)。
        # ⚠️ 不能用"applied 的后代"：applied=0076 时，0074/0075 是它的【祖先】，
        #    而本树缺的恰恰是 0074/0075/0076 三个 ⇒ 用后代会只算出 0076。
        missing: list[str] = []
        for other_raw in args.other_tree:
            other = Path(other_raw).resolve()
            other_local = set(_local_revisions(other))
            _, other_ancestors = _heads_and_ancestors(other)
            for rev in (other_local & other_ancestors) - set(local):
                missing.append(rev)
        missing = sorted(set(missing))

        verdict = "ALLOW_START" if compatible else "REJECT_START"
        action = (
            "允许启动（observe 档：只报告，不拒绝）" if args.mode == "observe" else
            ("允许启动" if compatible else "拒绝启动")
        )
        findings.append(
            {
                "event": "schema_guard",
                "mode": args.mode,
                "tree": str(tree),
                "db_version": applied,
                "tree_head": heads,
                "known": not unknown,
                "ancestor": not not_ancestor,
                "compatible": compatible,
                "missing_revisions": missing,
                "action": action,
            }
        )
        if not args.json:
            print(f"== {tree}")
            print(f"   本树 revision 数 : {len(local)}")
            print(f"   本树 head         : {heads}")
            print(f"   判据① 应用库版本在本树内?    : {not unknown}   (unknown={unknown})")
            print(f"   判据② 应用库版本是 head 祖先? : {not not_ancestor} (not_ancestor={not_ancestor})")
            if missing:
                print(f"   本树缺少的 revision : {missing}")
            print(f"   ⇒ 守卫判定: {verdict}  → {action}")
            print()

    if args.json:
        print(json.dumps(
            {"db_version": applied, "mode": args.mode, "findings": findings},
            ensure_ascii=False, indent=2, default=str,
        ))
    else:
        print("汇总:")
        for f in findings:
            print(f"   {f['action']:<34} {f['tree']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
