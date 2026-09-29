"""给 tech-doc/enums/ 下含 🟡/🔴 等级值的文件，在每个"取值/实测"段末尾加 "## ⚠️ 未固化值速查" 段。

约定见 tech-doc/enums/conventions.md §3.3。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ENUM_DIR = Path(__file__).resolve().parent.parent / "tech-doc" / "enums"

TAKE_VALUES_HEADING_RE = re.compile(r"^(#{1,3})\s*(.*?取值|实测样本|已知值|实测)")


def parse_table_rows_in_range(text_lines: list[str], start: int, end: int) -> list[dict]:
    """在 [start, end) 行范围内解析含 '等级' 列的表，收集 🟡/🔴 行的元组。"""
    rows: list[dict] = []
    in_take = False
    for i in range(start, min(end, len(text_lines))):
        line = text_lines[i]
        m = re.match(r"^#{1,3}\s", line)
        if m and not in_take:
            continue
        if m:
            in_take = False
            continue
        if line.startswith("|") and not line.startswith("|---"):
            cols = [c.strip() for c in line.split("|") if c.strip() != ""]
            if not cols:
                continue
            status = cols[0]
            if status not in ("🟡", "🔴"):
                continue
            entry = {
                "status": status,
                "value": cols[1] if len(cols) > 1 else "",
                "meaning": cols[2] if len(cols) > 2 else "",
            }
            if entry["value"] and entry["value"] != "—":
                rows.append(entry)
    return rows


def find_take_sections(lines: list[str]) -> list[tuple[int, int, int]]:
    """返回 (start_line, end_line, level) 列表，标识每个"取值"段的起止。"""
    sections = []
    current_start = None
    current_level = None
    for i, line in enumerate(lines):
        m = TAKE_VALUES_HEADING_RE.match(line)
        if m:
            current_start = i + 1  # 段标题在 i，段内容从 i+1 开始
            current_level = len(m.group(1))
            continue
        if current_start is not None:
            sub_m = re.match(r"^(#{1,3})\s", line)
            if sub_m and len(sub_m.group(1)) <= current_level:
                # 遇到同级或更高级 heading，结束当前段
                sections.append((current_start, i, current_level))
                current_start = None
                current_level = None
    # 收尾
    if current_start is not None:
        sections.append((current_start, len(lines), current_level))
    return sections


def build_summary(rows: list[dict]) -> str:
    if not rows:
        return ""
    yellow = [r for r in rows if r["status"] == "🟡"]
    red = [r for r in rows if r["status"] == "🔴"]
    lines = ["## ⚠️ 未固化值速查", ""]
    if yellow:
        lines.append(f"- 🟡 **{len(yellow)} 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。")
        for r in yellow[:5]:
            meaning = r["meaning"][:60]
            lines.append(f"  - 🟡 `{r['value']}` — {meaning}{'…' if len(r['meaning']) > 60 else ''}")
        if len(yellow) > 5:
            lines.append(f"  - …（其余 {len(yellow) - 5} 个见下方\"## 取值\"表）")
        lines.append("")
    if red:
        lines.append(f"- 🔴 **{len(red)} 个值未观测** —— 枚举可能存在但本项目无样本，禁止拍脑袋假设。")
        for r in red[:5]:
            meaning = r["meaning"][:60] or "（无含义描述）"
            lines.append(f"  - 🔴 `{r['value']}` — {meaning}{'…' if len(r['meaning']) > 60 else ''}")
        if len(red) > 5:
            lines.append(f"  - …（其余 {len(red) - 5} 个见下方\"## 取值\"表）")
        lines.append("")
    return "\n".join(lines)


def process_file(path: Path, dry_run: bool) -> tuple[bool, str]:
    """返回 (是否变更, 状态信息)。"""
    text = path.read_text()
    if "## ⚠️ 未固化值速查" in text:
        return False, f"⏭️  {path.name}: 已有 '⚠️ 未固化值速查' 段，跳过"

    lines = text.split("\n")
    sections = find_take_sections(lines)
    if not sections:
        return False, f"⏭️  {path.name}: 无取值段，跳过"

    # 倒序处理（从后往前，避免行号偏移）
    edits_made = False
    for start, end, level in reversed(sections):
        rows = parse_table_rows_in_range(lines, start, end)
        if not rows:
            continue
        summary = build_summary(rows)
        if not summary:
            continue
        # 插入到 end 之前
        summary_lines = summary.split("\n")
        new_lines = lines[:end] + summary_lines + [""] + lines[end:]
        lines = new_lines
        edits_made = True

    if not edits_made:
        return False, f"⏭️  {path.name}: 无 🟡/🔴 值可总结"

    if not dry_run:
        path.write_text("\n".join(lines))
    return True, f"✓  {path.name}: 加入速查段"


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    files = sorted(ENUM_DIR.glob("*.md"))
    files_changed = 0
    for path in files:
        if path.name in ("README.md", "conventions.md"):
            continue
        changed, msg = process_file(path, dry_run)
        print(msg)
        if changed:
            files_changed += 1
    print(f"\n=== 总结 ===")
    print(f"变更文件: {files_changed}")
    if dry_run:
        print("(dry-run 模式，未实际写文件)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
