# 退货/退款作业：同页重复上游 id 的入库行为与金额影响（2026-11-10）

> 执行人：测试工程师（bot_d330a0b9a748）｜委托：Chief
> 测试机：HP desk `schan@192.168.1.2`（`ssh hp-desk`）
> 调查基准：`origin/master` @ `9ecea05f`
> **本轮只读调查，未改任何生产代码。** 探针是一次性文件，跑完已从仓库移出（`git status` 干净）。

---

## 0. 结论速览

| 问题 | 结论 |
| --- | --- |
| 同一上游 id 在同一页内出现两次，会写成两行吗？ | **不会。** `after_sales.cases` 与 `after_sales.case_lines` 各只有 **1 行**（唯一键 + `ON CONFLICT DO UPDATE` ⇒ 覆盖而非新增） |
| 会被静默去重吗？ | **会（但只覆盖数值，不覆盖计数）。** case/case_lines 被覆盖去重；**`rows_total` / `rows_inserted` 仍按上游条数计** ⇒ 进度计数虚高一倍；**`raw_records` 也真的多写一条** |
| 退款金额会被重复计入吗？ | **取决于重复项的 payload 是否一致**（见下） |
| 对财务口径的影响面 | 利润读 `sum(after_sales.case_lines.refund_amount)`，再取 `greatest(\|结算退款\|, 确认退款)` ⇒ **重复计入会让净利/ROI 偏低**（退款是冲减项），并可能推着 `final_refund_vnd >= order_gmv_vnd` 分支更容易命中 |

**金额那一条的分叉（实测）**：

- **payload 完全相同的重复**（上游自己吐两次一模一样的记录）⇒ **金额不被重复计入**，与不重复的对照组**完全一致**。
- **payload 有差异的重复**（两版的明细行/金额不同）⇒ **明细行取「并集」** ⇒ 同一单的退款金额变成两条记录之和 ⇒ **会被重复计入利润**。实测：上游两版分别是 5.00 和 9.00 → 入库合计 **14.00**。

⇒ **Chief 描述的「A 店 85 条里 3 条重复、B 店 131 条里 7 条重复」是否影响金额，取决于那 10 个重复 id 的两版 payload 是否一致。
若一致 ⇒ 金额零影响，仅计数与审计流水虚高；若不一致 ⇒ 那几单的退款金额偏高，净利/ROI 偏低。**

---

## 1. 落库路径（代码事实）

### 1.1 分页层不去重

`tts_erp_v2/jobs/tiktok/after_sales.py::_walk_pages`（L159）只防「上游 `next_page_token` 重复」的分页死循环，
**对条目 id 不做任何去重**：

```python
seen_tokens: set[str] = set()
...
yield from items                       # ← 原样吐出，同页重复 id 直接穿过
next_token = data.get("next_page_token") or None
if not next_token:
    break
if next_token in seen_tokens:
    raise UpstreamJobError(...)
```

### 1.2 逐条处理 + 两处 upsert

`_process_one_type`（L285）对每个 raw 条目走一遍：`total += 1` → 解析 → 解析订单 → 插 `RawRecord` → upsert `Case` → upsert `CaseLine` → `inserted += 1`。

```python
session.execute(
    pg_insert(Case)
    .values(**insert_values)
    .on_conflict_do_update(
        index_elements=["shop_pk", "external_case_id"],
        set_=update_cols,
    )
)
...
session.execute(
    pg_insert(CaseLine)
    .values(**line_values)
    .on_conflict_do_update(
        index_elements=["case_id", "external_case_line_id"],
        set_={k: line_values[k] for k in line_values if k != "case_id"},
    )
)
```

### 1.3 唯一键与 DDL 对得上

| 表 | 唯一约束 | 出处 |
| --- | --- | --- |
| `after_sales.cases` | `UNIQUE (shop_pk, external_case_id)`（`uq_cases_account_ext`） | `alembic/versions/20260829_init_nine_schemas.py` + ORM `UniqueConstraint` |
| `after_sales.case_lines` | `UNIQUE (case_id, external_case_line_id)`（`uq_case_lines_case_ext`） | 同上 |

⇒ `index_elements` 与真实约束一致，重复 id 走 **DO UPDATE（覆盖）**，不会新增行。
`external_case_line_id` 可空，但代码在 `None` 时直接 `failed += 1` 并记 `PARSE_ERROR` 后 `continue` ⇒ **不会写 NULL 键的行**。

### 1.4 金额进利润的路径

```
tts_erp_v2/analytics/spu_profitability/_implementation.py:181-183
    confirmed_refunds AS (
        SELECT sl.id AS sales_order_line_id,
               coalesce(sum(cl.refund_amount), 0) AS confirmed_refund_vnd
        FROM after_sales.case_lines cl ...
    )
...
_implementation.py:310-313
    greatest(
        abs(coalesce(settled.customer_refund_vnd, 0)),
        coalesce(case_orders.confirmed_refund_amount, 0)
    ) AS final_refund_vnd
```

⇒ **退款是「行级求和」后与结算退款取较大值**；退款是冲减项 ⇒ 退款被高估 ⇒ **净利/ROI 偏低**。

---

## 2. 实跑对照（真实输出）

探针：在 `origin/master` 的独立 worktree 上，用 `tests/jobs_tiktok/test_after_sales_job.py` 的现成夹具
（`_make_account_with_order` / `invoke_seeded_job`），构造单页内重复的 `returns` 列表，逐场景统计入库行数与金额。

```
$ bash scripts/test_isolated.sh fast tests/jobs_tiktok/test_tmp_dup_probe.py -s
EXIT=0 ELAPSED=6s

PROBE|A_same_payload|cases=1|case_lines=1|raw_records=2|sum_line_refund=5.0000|sum_head_refund=0|rows_total=2|rows_inserted=2|rows_failed=0|lines=CL1|amounts=CL1=5.0000
PROBE|B_control_single|cases=1|case_lines=1|raw_records=3|sum_line_refund=5.0000|sum_head_refund=0|rows_total=1|rows_inserted=1|rows_failed=0|lines=CL1|amounts=CL1=5.0000
PROBE|C_same_line_diff_amount|cases=1|case_lines=1|raw_records=5|sum_line_refund=9.0000|sum_head_refund=0|rows_total=2|rows_inserted=2|rows_failed=0|lines=CL1|amounts=CL1=9.0000
PROBE|D_diff_line_ids|cases=1|case_lines=2|raw_records=7|sum_line_refund=14.0000|sum_head_refund=0|rows_total=2|rows_inserted=2|rows_failed=0|lines=CL1,CL2|amounts=CL1=5.0000,CL2=9.0000
PROBE|E_second_has_both_lines|cases=1|case_lines=2|raw_records=9|sum_line_refund=16.0000|sum_head_refund=0|rows_total=2|rows_inserted=2|rows_failed=0|lines=CL1,CL2|amounts=CL1=7.0000,CL2=9.0000
```

> `raw_records` 是同一 session 内累计值（场景递增 2 / 1 / 2 / 2 / 2）⇒ **每个重复项真的多写一条原始 payload 记录**。

### 2.1 逐场景对照

| 场景 | 上游输入 | cases | case_lines | 退款金额合计 | rows_total | rows_inserted | 判读 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **A** | 同 id 同 payload ×2 | 1 | 1 | **5.0000** | 2 | 2 | 与对照组 B 一致 ⇒ **金额不重复计入**；计数虚高一倍；raw_records 多 1 条 |
| **B** | 对照：单条 | 1 | 1 | 5.0000 | 1 | 1 | 基准 |
| **C** | 同 id、同 line id、金额 5→9 | 1 | 1 | **9.0000** | 2 | 2 | **最后一条赢** ⇒ 金额取决于上游重复项的顺序 |
| **D** | 同 id、两版各带一行（CL1 5.00 / CL2 9.00，两行都在订单上） | 1 | **2** | **14.0000**（= 5+9） | 2 | 2 | **明细行取并集 ⇒ 金额被重复计入** |
| **E** | 同 id、第二版带两行（CL1 7.00 / CL2 9.00） | 1 | **2** | **16.0000**（= 7+9） | 2 | 2 | 同上；CL1 被覆盖成 7.00，CL2 是第一版没有的 ⇒ 并集 |

### 2.2 用 Chief 给的样本量算量级

| 店铺 | 上游条数 | 重复 id 数 | 去重后逻辑 case | 比例 | 金额影响 |
| --- | --- | --- | --- | --- | --- |
| A 店 | 85 | 3 | 82 | −3.5% | payload 一致 ⇒ **0**；不一致 ⇒ 那 3 单各偏高至多 1 倍 |
| B 店 | 131 | 7 | 124 | −5.3% | payload 一致 ⇒ **0**；不一致 ⇒ 那 7 单各偏高至多 1 倍 |

⇒ **「上游条数」比「实际单数」多 3.5% / 5.3%，但金额影响取决于重复项两版是否一致。**
不一致时的最坏情况：这 3 + 7 = 10 单的退款金额被算成两笔 ⇒ 该 10 单的退款最多翻倍。

### 2.3 计数虚高的影响面

`rows_total` / `rows_inserted` 落到 `integration.sync_jobs`，用于进度与「作业是否真的在跑」的监控
（如 `scripts/check_jobs_actually_run.py` 按 tick 比较 `rows_inserted`）。**不参与金额计算**。
水位线用的是 `max_update_ms`（只取成功解析的条目），重复项同样解析成功 ⇒ **水位线不受影响**。

---

## 3. 三个问题的直接回答

**Q1 会被写成两行吗？**
`cases` / `case_lines` **不会**（唯一键 + DO UPDATE 覆盖）。**但 `raw_records` 会**——每个重复项一条原始 payload 记录，
审计流水里同一个上游 id 会出现两次。

**Q2 会被静默去重吗？**
**数值上会**（后一条覆盖前一条），**但没有"去重"的语义**：
- 覆盖的是同一行的字段，不是"跳过重复项"；
- 明细行是**逐行 upsert**，所以第一版里有、第二版里没有的行**会存活** ⇒ 并集；
- 计数完全不知道发生过去重（`rows_total`/`rows_inserted` 照实数上游条数）。

**Q3 对财务口径的影响面？**
`sum(case_lines.refund_amount)` → `greatest(|结算退款|, 确认退款)` → `final_refund_vnd` → 冲减 `net_revenue_cny` / `net_profit_cny` / ROI。
并集场景下退款被高估 ⇒ **净利与 ROI 偏低**（方向与"多算收入"相反）；同时
`AND final_refund_vnd >= order_gmv_vnd` 这个分支更容易命中（`_implementation.py:395, 762`）。

---

## 4. 修法选项与覆盖差异

| 选项 | 做法 | 覆盖**同页**重复 | 覆盖**跨页**重复 | 消除并集金额风险 | 计数真实 | 动哪些文件 |
| --- | --- | --- | --- | --- | --- | --- |
| **A. 作业内页级去重** | `_process_one_type` 入口按 `(shop_pk, external_case_id)` 用 dict 折叠（后一条赢） | ✓ | ✗（每页一个 dict） | ✓（同页） | ✗ | `tts_erp_v2/jobs/tiktok/after_sales.py` |
| **B. 分页层跨页去重** | `_walk_pages` 维护 `seen_case_ids`，整条 walk 只吐每个 id 一次 | ✓ | ✓ | ✓ | ✗ | 同上 |
| **C. 落库层幂等 + 真实计数** | 保留 upsert；`rows_inserted` 改成按 `xmax=0` 判定真实新增/更新 | ✗ | ✗ | ✗ | ✓ | 同上 |
| **D. A + C** | 页内折叠 + 真实计数 | ✓ | ✗ | ✓（同页） | ✓ | 同上 |
| **E. B + C** | 跨页折叠 + 真实计数 | ✓ | ✓ | ✓ | ✓ | 同上 |

**建议 E**：

- **B 消除金额风险**：并集只在「同一个 id 的两版行同时落库」时产生；只要整条 walk 里每个 id 只吐一次，
  上游两版谁赢就固定为"先到的那版"（或按 update_time 取新），**不会再出现 5+9=14 这种加法**。
- **C 让计数说真话**：`rows_inserted` 现在把 DO UPDATE 也算成 inserted，重复项让进度虚高一倍；
  用 `xmax = 0` 区分真新增与更新后，监控与 `check_jobs_actually_run.py` 的判断才有意义。
- **A 是 B 的弱化版**：只解决同页。若上游改成跨页重复（分页顺序变化就会发生），A 挡不住并集。

**成本与风险**：

- B 需要在 walk 期间保留已见 id 集合 ⇒ 内存随窗口增长（A 店 85 / B 店 131 条的规模可忽略；
  但若窗口调到数千条，需要评估——注意 `_walk_pages` 已经是流式产出，当前设计刻意只驻留一页）。
- 更省内存的替代：**不靠内存去重，靠落库语义**——把 `case_lines` 改成"整单替换"
  （收到一个 case 就先删该 case 旧行再插新行），则并集不可能出现；代价是每次重写都多一轮 DML。
- 根因在上游（同页自带重复 id），作业侧只能防御。若上游能修，优先推上游。

**不推荐**：

- ❌ 只放宽/收紧 `ON CONFLICT`（改成 `DO NOTHING`）——那样会保留**第一条**的金额，
  而上游第二版可能是更新的那一版 ⇒ **金额变成"旧的"，比并集更难发现**。
- ❌ 在金额侧做"取 max/min"兜底——那是把上游数据问题藏进业务口径。

---

## 5. 未执行 / 未覆盖

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 改任何生产代码 | 未执行 | 本轮只出结论 |
| 取 A 店 / B 店真实上游 payload 比对两版是否一致 | 未执行 | 需要真实 TikTok 凭据与代理通道，且本轮禁止连生产形状库；⇒ 结论按「两版一致 / 不一致」两种情况分别给出 |
| 在生产形状库上只读核对真实重复 id 的两版差异 | 未执行 | 同上（且要求显式 read-only） |
| 跨页重复的实跑验证 | 未执行 | 只构造了同页重复；跨页场景在代码上走同一条 upsert 路径，差别只在于 A/B 两种去重层的覆盖范围 |
| `raw_records` 是否有下游消费者依赖"每条上游记录一行" | 未执行 | 本轮未追 |

---

## 6. 现场

| 路径 | 说明 |
| --- | --- |
| `/home/schan/tts-erp/.worktrees/probe-after-sales-20261110` | 探针 worktree（`origin/master` @ `9ecea05f`），探针文件已移出，`git status` 干净 |
| `/tmp/TEST_tmp_dup_probe.py.bak` | 探针源文件（一次性，不入库） |
| `/tmp/TEST_dup_probe.log` | 探针运行原始日志 |
| `tests/jobs_tiktok/test_after_sales_job.py` | 复用其夹具的既有测试文件（未改动） |
