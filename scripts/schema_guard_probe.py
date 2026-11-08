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
    """用 alembic 自己的 ScriptDirectory 求 heads 与祖先集合。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(tree / "alembic"))
    cfg.set_main_option("version_locations", str(tree / "alembic" / "versions"))
    try:
        script = ScriptDirectory.from_config(cfg)
        heads = list(script.get_heads())
        # 不用 walk_revisions(head, "base")：本仓的 revision 图存在分叉
        #（0037_merge_0036_heads / 0067_merge_price_and_publish 是 tuple 形
        #  down_revision），alembic 会报 "Requested range ... does not refer to
        #  ancestor/descendant revisions along the same branch"。
        # 改为按 down_revision 自己走链，并对 tuple 的每个分支都走一遍。
        all_revs = list(script.walk_revisions())
        down = {r.revision: r.down_revision for r in all_revs}
        ancestors: set[str] = set()
        for head in heads:
            stack = [head]
            while stack:
                cur = stack.pop()
                if not cur or cur in ancestors:
                    continue
                ancestors.add(cur)
                nxt = down.get(cur)
                if isinstance(nxt, (list, tuple)):
                    stack.extend(n for n in nxt if n)
                elif nxt:
                    stack.append(nxt)
        return heads, ancestors
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"alembic ScriptDirectory failed for {tree}: {exc}\n")
        return [], set()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tree", action="append", required=True, help="要检查的代码树（可多次）")
    args = ap.parse_args(argv)

    applied = _db_version()
    print(f"生产库 alembic_version = {applied}")
    print()

    verdicts = []
    for raw in args.tree:
        tree = Path(raw).resolve()
        local = _local_revisions(tree)
        heads, ancestors = _heads_and_ancestors(tree)
        unknown = applied is not None and applied not in local
        not_ancestor = applied is not None and applied not in ancestors
        verdict = "REJECT_START" if (unknown or not_ancestor) else "ALLOW_START"
        print(f"== {tree}")
        print(f"   本树 revision 数 : {len(local)}")
        print(f"   本树 head         : {heads}")
        print(f"   判据① 应用库版本在本树内? : {not unknown}   (unknown={unknown})")
        print(f"   判据② 应用库版本是 head 祖先? : {not not_ancestor} (not_ancestor={not_ancestor})")
        print(f"   ⇒ 守卫判定: {verdict}")
        print()
        verdicts.append((str(tree), verdict))

    print("汇总:")
    for t, v in verdicts:
        print(f"   {v:<12} {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
