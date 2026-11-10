"""Stop-write record for the retired SPU old-threshold alert evaluator.

After the release-line deployment that removed the
``analytics.spu_profit_deterioration_alert`` job registration, this script
rewrites the two documents that describe the evaluator's status:

  1. ``docs/handoff/topic-directory-index.md``   (sections 1, 2, 5, 5.1, 6)
  2. ``docs/spu-profit-deterioration/01-product-proposal.md``  (header)

The wording it writes is "已停写" (stopped writing) — **not** "已归档"
(archived). The snapshot table ``analytics.spu_deterioration_alerts`` and its
historical rows are deliberately kept; dropping the table is a destructive
operation that needs its own authorisation and migration.

Usage:
  python scripts/oneoff_record_spu_alert_stop_write.py --check
  python scripts/oneoff_record_spu_alert_stop_write.py --apply

  --check  report which edits would be applied, write nothing (default)
  --apply  apply the edits

Safety properties:

* **It refuses to run when the code-side precondition is missing.** The script
  requires that ``sync_worker/scheduler.py`` in this tree carries no live
  ``JobSpec`` registration of the old evaluator. If one is present, the
  documents would be claiming "stopped" ahead of the code, so the script
  aborts. Deploy first, then run this.
* **It is idempotent.** Re-running on an already-updated tree reports every
  edit as skipped and changes nothing.

Note: the deployment target is the release tree
(``/home/schan/tts-erp-releases/erp-runtime-secret-84ec0a49``), whose HEAD
must equal ``TTS_ERP_DEPLOYED_COMMIT``. Run this from a checkout of that
committed tree, not from an arbitrary working copy.
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

INDEX = Path("docs/handoff/topic-directory-index.md")
PRD = Path("docs/spu-profit-deterioration/01-product-proposal.md")
SCHEDULER = Path("tts_erp_v2/sync_worker/scheduler.py")

applied: list[str] = []
skipped: list[str] = []


def fail(msg: str) -> None:
    print(f"ABORT: {msg}")
    sys.exit(1)


def apply(text: str, old: str, new: str, label: str) -> str:
    """Replace old->new once. If new is already present, treat as already applied."""
    if new in text:
        skipped.append(label)
        return text
    if old not in text:
        fail(f"{label} anchor not found (neither old nor new form present)")
    applied.append(label)
    return text.replace(old, new, 1)


def main() -> int:
    check_only = "--apply" not in sys.argv
    if not check_only and "--check" in sys.argv:
        check_only = True

    # --- precondition: the code must already be stopped in this tree ----------
    scheduler = SCHEDULER.read_text(encoding="utf-8")
    live = re.search(
        r'["\']analytics\.spu_profit_deterioration_alert["\']\s*:\s*JobSpec\(',
        scheduler,
    )
    if live:
        fail(
            "scheduler.py still registers analytics.spu_profit_deterioration_alert "
            "(a live JobSpec entry was found). Stop-write has NOT landed in this "
            "tree — deploy the release line first, then run this script."
        )
    print("OK precondition: no live JobSpec registration of the old evaluator")

    # --- 1. topic-directory-index.md ----------------------------------------
    s = INDEX.read_text(encoding="utf-8")

    s = apply(
        s,
        """**两个完整四段式目录的内容对齐情况：文件齐，口径也齐。** 早前版本称 `spu-profit-deterioration/`「文件齐了但内容口径没齐」，该结论已按 02/03/04 头部修订声明改正——详见 §2 末尾。需要单独注意的是**代码运行状态**（旧阈值 evaluator 写侧仍在生产跑），那不是文档口径问题。""",
        """**两个完整四段式目录的内容对齐情况：文件齐，口径也齐。** 早前版本称 `spu-profit-deterioration/`「文件齐了但内容口径没齐」，该结论已按 02/03/04 头部修订声明改正——详见 §2 末尾。需要单独注意的是**代码运行状态**：旧阈值 evaluator 曾长期「读侧已退役、写侧仍在生产跑」，现已停写——详见 §5.1。""",
        "index §1 summary",
    )

    s = apply(
        s,
        """**仍需知道的一处真实状态差异（不是文档口径问题，是代码运行状态）：** 旧阈值告警方案在代码里处于**「读侧已退役、写侧仍在生产跑」**的半退役状态，详见 §5 的对应条目。文档已对齐，但旧 evaluator 代码仍在线并每天产出快照。""",
        """**旧阈值告警方案当前状态（2026-11-11 更新）：读侧已退役、写侧已停写。** 该方案曾处于「读侧已退役、写侧仍在生产跑」的半退役状态；旧 evaluator 的 scheduler 注册已撤销，生产侧不再产出快照。快照表 `analytics.spu_deterioration_alerts` **保留未删**（删表属破坏性操作，需另行授权）。详见 §5.1。""",
        "index §2 note",
    )

    s = apply(
        s,
        """| SPU 利润劣化告警（旧方案） | `docs/design/spu-profit-deterioration-alert.md`、`docs/aegis/plans/2026-10-05-spu-profit-deterioration-alert.md` | SPU 盈利、阈值告警 | **不能归档，只能标注**（2026-11-08 核实，详见下方说明） |""",
        """| SPU 利润劣化告警（旧方案） | `docs/design/spu-profit-deterioration-alert.md`、`docs/aegis/plans/2026-10-05-spu-profit-deterioration-alert.md` | SPU 盈利、阈值告警 | **已停写**（2026-11-11 部署后更新，详见下方说明）；文档可归档与否见 §5.1 |""",
        "index §5 row",
    )

    s = apply(
        s,
        """### 5.1 「SPU 利润劣化告警（旧方案）」为什么只能标注、不能归档""",
        """### 5.1 「SPU 利润劣化告警（旧方案）」：已停写，为什么还不能归档""",
        "index §5.1 heading",
    )

    s = apply(
        s,
        """旧方案（severity / sample_status / confirmation_status / thresholds / fast|confirmation）**在代码里处于「读侧已退役、写侧仍在生产跑」的半退役状态**，不是死代码。归档它等于文档说一套、代码跑另一套。""",
        """旧方案（severity / sample_status / confirmation_status / thresholds / fast|confirmation）**已于 2026-11-11 停写**：scheduler 中的 job 注册已撤销，生产侧不再每天产出快照。

**停写范围（重要）**：只撤销 `sync_worker/scheduler.py` 里的 job 注册。**不删** job 模块（`tts_erp_v2/jobs/spu_deterioration_alert.py`）、**不删** `analytics.spu_deterioration_alerts` 表、**不删** 历史快照数据。删表属破坏性操作，需用户单独授权后另开 migration。已新增护栏断言防止该 job 被静默重新注册。""",
        "index §5.1 head",
    )

    s = apply(
        s,
        """**写侧仍在生产跑（已核实）：**

- `sync_worker/scheduler.py` 仍注册 `analytics.spu_profit_deterioration_alert`（`interval_seconds=86400`）→ `tts_erp_v2/jobs/spu_deterioration_alert.py` → `read.py` 的 `evaluate()` → `materialize.py` 落库 `analytics.spu_deterioration_alerts`（表模型 `tts_erp_v2/db/models/analytics.py:29`，索引仍含 severity）。
- 该 runtime config key `analytics.spu_profit_deterioration_alert.v1` 仍接通用 runtime-config API（create/draft/publish/rollback，见 `api/v2/config.py`、`runtime_config/workflow.py`）。

**因此本索引的处理方式：** §5 该行只写「读侧已退役、写侧仍在运行、快照表无读取方」，**不写「已归档」**。旧方案文档本身也不移入 `docs/archive/`——归档状态与实际运行状态不符，比不归档更危险。""",
        """**写侧：停写前在生产跑，现已停写（时间线）**

停写前的状态（2026-11-08 实测，生产形状库）：

- `sync_worker/scheduler.py` 注册 `analytics.spu_profit_deterioration_alert`（`interval_seconds=86400`）→ `tts_erp_v2/jobs/spu_deterioration_alert.py` → `read.py` 的 `evaluate()` → `materialize.py` 落库 `analytics.spu_deterioration_alerts`（表模型 `tts_erp_v2/db/models/analytics.py:29`，索引仍含 severity）。每天产出 1788 行，锚点是店铺本地 T-1。
- 该 runtime config key `analytics.spu_profit_deterioration_alert.v1` 仍接通用 runtime-config API（create/draft/publish/rollback，见 `api/v2/config.py`、`runtime_config/workflow.py`）。

停写后的状态（2026-11-11 部署后）：

- scheduler 中**不再有该 job 的活注册**（生产树 `sync_worker/scheduler.py` 实测 0 命中），每日不再产出快照。
- **job 模块、快照表、历史数据全部保留**——停写只是不跑，不是删除。
- runtime config API 的接线**仍在**（`api/v2/config.py` 的校验分支未撤）：该 key 仍可被 publish/draft。**这不产生业务影响**（没有 job 会消费它），但若将来有别的消费方接入，必须重新审视——这已由 §6 第 9 条的规则覆盖。

**因此本索引的处理方式：** §5 该行写「已停写」，并注明快照表保留未删；**仍不写「已归档」**。旧方案文档本身也不移入 `docs/archive/`——归档会移动既有文档位置并影响 `docs/aegis/plans/` 的历史证据链，且用户尚未单独决定，**归档状态与实际状态不符，比不归档更危险**。""",
        "index §5.1 write-side",
    )

    s = apply(
        s,
        """**待决事项 —— 旧 evaluator 是否停写（2026-11-08：状态 = 待决，用户尚未确认）**

开发工程师 2026-11-08 核查生产形状库时发现，该 config key 的 `published_version` 为空、`runtime_config_revisions` 表无该 key 的任何 revision，只有 `draft_version=1`，实际生效的是代码内 `SEED_FALLBACK_CONFIG`（回测暂定、enabled=true），落库 `effective_config_source=seed_fallback`。即：**这套阈值从未被人正式 publish，却每天在生产上产出判定，而快照表已无读取方。**

| 事项 | 当前状态 |
| --- | --- |
| 旧 evaluator 是否停写（撤销 scheduler 注册 / 停跑该 job） | **待决** —— 属产品/运维授权动作，**本索引不预设结论** |
| 旧方案文档是否归档 | **待决** —— 归档与「写侧是否仍运行」联动，不能先于停写决定 |
| 阈值配置是否补一次正式 publish | **待决** —— 若不停写，需决定是把 `SEED_FALLBACK_CONFIG` 正式发布，还是承认它只是回测暂定值 |

**处置纪律（无论最后怎么定）：** 决定落地前，本文与 PRD 01 一律按「读侧已退役、写侧仍在运行」表述；
**不写「已归档」**，也不写「已废弃」。决定做出并落地后，再回填本表并同步 §5 该行。""",
        """**停写决策与执行记录**

用户于 2026-11-09 确认停写；2026-11-11 完成部署。

| 事项 | 状态 |
| --- | --- |
| 旧 evaluator 是否停写（撤销 scheduler 注册 / 停跑该 job） | **已停写** —— 2026-11-11 部署到生产树后生效；`sync_worker/scheduler.py` 中已无该 job 的活注册，并加了防静默重注册的护栏断言 |
| 快照表 `analytics.spu_deterioration_alerts` 与历史数据 | **保留未删** —— 删表属破坏性操作，未获单独授权 |
| 旧方案文档是否归档 | **待决** —— 代码已停写，但归档会移动既有文档位置并影响 `docs/aegis/plans/` 的历史证据链，留待用户决定；**在决定前仍不写「已归档」** |
| 阈值配置是否补一次正式 publish | **不再需要** —— 既然不再运行，就没有「发布一份生产阈值」的需求；`SEED_FALLBACK_CONFIG` 随停写一起退役 |

**关于那次「从未 publish 却每天产出判定」**：2026-11-08 核查发现该 config key 的 `published_version` 为空、`runtime_config_revisions` 表无任何 revision，实际生效的是代码内 `SEED_FALLBACK_CONFIG`（回测暂定、enabled=true），落库 `effective_config_source=seed_fallback`。停写已使这一状态不再产生业务影响，保留此记录作为「回测暂定值不应直接充当生产配置」的历史依据。

**处置纪律（继续有效）：** 文档写「已停写」必须有**代码依据**——master 上无活注册 **且** 生产树已部署并重启 worker。两者缺一，文档仍写「读侧已退役、写侧仍在运行」。""",
        "index §5.1 decision table",
    )

    s = apply(
        s,
        """9. **「已归档」必须有代码依据**：写某个方案已归档前，先确认它在当前 `origin/master` 上确实不再运行。文档与代码不一致时，索引按代码事实写，并标注「读侧已退役、写侧仍在运行」这类半退役状态（见 §5.1）。""",
        """9. **「已停写」与「已归档」是两件事，各自都要有依据**：
   - 写「已停写」前，必须确认**两处**：当前 `origin/master` 上无活注册 **且** 生产树已部署该 commit 并重启了 worker。缺任一条，文档仍写「读侧已退役、写侧仍在运行」。
   - 写「已归档」前，除了代码不再运行，还要确认**移动文档不会破坏历史证据链**（被取代方案常被 `docs/aegis/plans/` 等处引用）。
   - 文档与代码不一致时，索引按代码事实写，并标注「读侧已退役、写侧仍在运行」这类半退役状态（见 §5.1）。""",
        "index §6 rule 9",
    )

    # --- 2. PRD 01 header ----------------------------------------------------
    p = PRD.read_text(encoding="utf-8")

    p = apply(
        p,
        """> 补充（2026-11-08 核实）：旧阈值 evaluator 的**读侧已退役**（HTTP 接口不返回 severity/sample_status，前端已挡掉旧筛选参数），但**写侧仍在生产运行**（每日调度仍写 `analytics.spu_deterioration_alerts` 快照表，且该快照表已无读取方）。因此本 PRD 的产品口径不受影响，但旧 evaluator 代码尚未退役。
> **旧 evaluator 是否停写＝待决**（属产品/运维授权动作，用户尚未确认）。在决定落地前，本文与主题目录索引一律按「读侧已退役、写侧仍在运行」表述，**不写「已归档」/「已废弃」**。见 [`docs/handoff/topic-directory-index.md`](../handoff/topic-directory-index.md) §5.1。""",
        """> 补充（2026-11-11 更新）：旧阈值 evaluator **读侧已退役**（HTTP 接口不返回 severity/sample_status，前端已挡掉旧筛选参数），**写侧也已停写**——scheduler 中的 job 注册已撤销并部署到生产树，不再每天产出快照；快照表 `analytics.spu_deterioration_alerts` 保留未删。本 PRD 的产品口径自始未受旧方案影响。详见 [`docs/handoff/topic-directory-index.md`](../handoff/topic-directory-index.md) §5.1。""",
        "PRD 01 header",
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
    PRD.write_text(p, encoding="utf-8")
    print("DONE: both documents updated to the post-deployment wording.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
