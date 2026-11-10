"""Sync the topic-directory index with the media-asset-library theme start.

The media asset library theme has started: ``docs/media-asset-library/`` now
exists with its 01 product proposal. This script updates the index so it
reflects reality:

  - section 1 summary counts (a new "01 only" theme directory)
  - section 2/3 tables
  - section 5 candidate list (media asset library moves out of the
    "no topic directory yet" bucket)

Usage:
  python scripts/oneoff_sync_index_media_asset_theme.py --check
  python scripts/oneoff_sync_index_media_asset_theme.py --apply

Idempotent: re-running reports the edits as skipped and changes nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

INDEX = Path("docs/handoff/topic-directory-index.md")

applied: list[str] = []
skipped: list[str] = []


def fail(msg: str) -> None:
    print(f"ABORT: {msg}")
    sys.exit(1)


def apply(text: str, old: str, new: str, label: str) -> str:
    if new:
        if new in text:
            skipped.append(label)
            return text
    if old not in text:
        fail(f"{label} anchor not found (neither old nor new form present)")
    applied.append(label)
    return text.replace(old, new, 1)


def apply_delete(text: str, old: str, label: str) -> str:
    """Remove a block. Idempotent: if it is already gone, treat as applied."""
    if old not in text:
        skipped.append(label)
        return text
    applied.append(label)
    return text.replace(old, "", 1)


def main() -> int:
    check_only = "--apply" not in sys.argv

    if not Path("docs/media-asset-library/01-product-proposal.md").is_file():
        fail(
            "docs/media-asset-library/01-product-proposal.md is missing — the theme "
            "has not started yet, so the index must not list it as started."
        )
    print("OK precondition: docs/media-asset-library/01-product-proposal.md exists")

    s = INDEX.read_text(encoding="utf-8")

    # --- section 1 summary counts -------------------------------------------
    s = apply(
        s,
        """**按 `docs/<主题>/01..04` 四段式建的完整主题目录只有 2 个。**

| # | 结论 | 数量 | 主题 |
| --- | --- | --- | --- |
| 1 | 四段式齐全 | **2** | `spu-profit-deterioration`、`video-publish` |
| 2 | 编号齐全但与标准四段式不对齐 | **1** | `plugins/monitor`（01–05，且实际混了两个主题） |
| 3 | 只建了 01，缺 02/03/04 | **1** | `plugins/ads-data-sync` |""",
        """**按 `docs/<主题>/01..04` 四段式建的完整主题目录只有 2 个。**

| # | 结论 | 数量 | 主题 |
| --- | --- | --- | --- |
| 1 | 四段式齐全 | **2** | `spu-profit-deterioration`、`video-publish` |
| 2 | 编号齐全但与标准四段式不对齐 | **1** | `plugins/monitor`（01–05，且实际混了两个主题） |
| 3 | 只建了 01，缺 02/03/04 | **2** | `plugins/ads-data-sync`、`media-asset-library` |""",
        "index §1 count row 3",
    )

    s = apply(
        s,
        """| 5 | 有方案文档但**没建主题目录**的候选主题 | **9** | 媒体资产、运行配置、RunningHub、店铺入驻、前端迁移等（见 §5） |""",
        """| 5 | 有方案文档但**没建主题目录**的候选主题 | **8** | 运行配置、RunningHub、店铺入驻、前端迁移等（见 §5） |""",
        "index §1 count row 5",
    )

    # --- section 3: add the new "01 only" theme ------------------------------
    s = apply(
        s,
        """## 3. 编号存在但不完整或不对齐（2 个）

| 主题 | 路径 | 实际现状 | 涉及业务领域 | 一句话说明 |
| --- | --- | --- | --- | --- |""",
        """## 3. 编号存在但不完整或不对齐（3 个）

| 主题 | 路径 | 实际现状 | 涉及业务领域 | 一句话说明 |
| --- | --- | --- | --- | --- |
| 媒体资产库 / 素材包 | `docs/media-asset-library/` | 🟡 **只有 `01-product-proposal.md`**（2026-11-09 开工）；缺 02 技术设计 / 03 实施计划 / 04 测试计划 | 媒体资产、数字人档案、素材包、MinIO 对象存储 | 在全局媒体资产库里维护「有名字的视频分组」，数字人档案可引用同一个包、两处看到同一份内容。**代码已落地**（迁移 `0076_media_packs` + 7 个包端点 + React 页面），本主题是产品化收口：固化可验收契约并补齐测试覆盖 |""",
        "index §3 table head",
    )

    # --- section 5: media asset library moves out of the candidate bucket ----
    s = apply(
        s,
        """## 5. 有方案文档但还没建主题目录的候选主题（9 项）

这些主题的文档是真实存在的、且散落在 `design/` `plans/` `aegis/` `api/` `ops/` 里，但没有 `docs/<主题>/01..04` 目录。**新主题开工前，先查这里，避免重复造方案。**""",
        """## 5. 有方案文档但还没建主题目录的候选主题（8 项）

这些主题的文档是真实存在的、且散落在 `design/` `plans/` `aegis/` `api/` `ops/` 里，但没有 `docs/<主题>/01..04` 目录。**新主题开工前，先查这里，避免重复造方案。**

> **媒体资产库已移出本表**（2026-11-09 开工，主题目录 `docs/media-asset-library/` 已建并出了 01）。它的原始散落文档 `docs/design/media-asset-library.md` 等**留在原处不动**——设计文档是历史依据，`01` 是当前产品契约，两者都要可查。""",
        "index §5 intro",
    )

    s = apply_delete(
        s,
        """| 媒体资产库 / 数字人 | `docs/design/media-asset-library.md`、`docs/plans/2026-11-05-media-interaction-implementation.md`、`docs/plans/2026-11-08-media-packs.md` | 媒体资产、数字人、MinIO 对象存储 | 建 `docs/media-asset-library/`；design 迁 01，plans 迁 02/03 |
""",
        "index §5 media asset row",
    )

    mode = "CHECK ONLY" if check_only else "APPLY"
    print(f"mode={mode}")
    print(f"applied={len(applied)} skipped={len(skipped)}")
    for label in applied:
        print(f"  {'would apply' if check_only else 'applied'}: {label}")
    for label in skipped:
        print(f"  skipped (already applied): {label}")

    if check_only:
        print("CHECK DONE: nothing written. Re-run with --apply to write.")
        return 0

    INDEX.write_text(s, encoding="utf-8")
    print("DONE: index updated for the media-asset-library theme start.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
