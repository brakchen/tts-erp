# page_readiness 就绪判据体检与加固方案（2026-11-10）

> 执行人：开发工程师（bot_a7b5359e0d7c）｜委托：Chief
> 范围：**只出方案与实测结论，本轮不改代码、不提交**；实施等第三轮（e2e 迁移 + 停写合入 master）确认写域不冲突后再做。
> 基线：master `e3e85e0e`（探测时 origin/master 已到 `829572b7`）；测试机 `ssh hp-desk`。
> 本文所有判定均来自实机运行输出，不含推断。

---

## 0. 结论速览

| 项 | 结论 |
| --- | --- |
| 恒假的就绪判据 | **6 条，分布在 2 个 slug**：`spu-profit-deterioration` 3 条旧 DOM id、`runninghub` 3 条陈旧文案 |
| "命中但不可靠"的判据 | 1 条：`spu-profit-deterioration` 的 `TEST_RENDER_COMPARE_0`（靠 fixture 文案巧合，不是就绪信号） |
| 其余 16 个 slug | 判据全部命中（count ≥ 1），无同类问题 |
| 与在跑分支的写域冲突 | **无**。`origin/test/spu-page-browser-gate` 改 4 个文件，与本方案要碰的文件零交集 |
| 破坏性自验证 | **已实测**：破坏 fixture 的 spuId → 6 个用例红，错误是 30s 超时而不是清晰报错 |

---

## 1. `tests/support/page_readiness.py` 现状

文件全文 61 行，结构是"一串 if/elif + 一个 else 兜底"：

```
L9-13   TABLES = {8 个 slug → grid 的 aria-label}
L16     def wait_canned_page(page, slug)
L18-20  if slug == "spu-profit-deterioration" and page.locator("#alert-page").count():
            #alert-status[data-kind="ready"] / #alert-rows [data-spu-pk]
L21-26  elif slug == "runninghub": 3 条文案/checkbox 判据 + assert 无 alert
L27-28  else: page.locator("#frontend-root .erp-page").wait_for()
L29-30  if slug in TABLES: get_by_role("grid", name=..., exact=True)
L31-33  elif spu-roi/focused-spus: .tabulator-row + #data-freshness[data-source=ads]
L34-38  elif dashboard: region+2 grid+文案
L39-41  elif intercept-stats: grid + .uplot canvas
L42-43  elif runtime-configs: grid + TEST_RENDER_RUNTIME
L44-45  elif spu-profit-deterioration: get_by_text("TEST_RENDER_COMPARE_0")
L46-51  elif media-assets: 空态文案
L52-53  elif digital-humans: 空态文案
L54-57  elif video-publish: region + 2 文案 + grid
L58-59  else: raise AssertionError("Missing canned readiness contract: {slug}")
L60-61  document.fonts.ready + record_phase("browser_ready", …)
```

关键结构事实：**L18 的 `if` 排在最前**，且条件里带 `page.locator("#alert-page").count()`。React 页面不渲染这个 id ⇒ 条件恒为假 ⇒ 告警页落到 L44/L45 的 fixture 文案分支。

---

## 2. 逐条实测判定（运行时）

方法：临时探针（`tests/browser/tmp_probe_readiness.py`，**跑完已删除**）逐 slug 打开页面，先证明页面已渲染（`#frontend-root` 的 `childElementCount > 0`，实测全部 = 1），再逐条 count 判据选择器。

```bash
bash scripts/test_isolated.sh fast tests/browser/tmp_probe_readiness.py -s
```

### 2.1 恒假的判据（6 条）

| slug | 判据 | 实测 count |
| --- | --- | --- |
| spu-profit-deterioration | L18 `#alert-page` | **0** |
| spu-profit-deterioration | L19 `#alert-status[data-kind="ready"]` | **0** |
| spu-profit-deterioration | L20 `#alert-rows [data-spu-pk]` | **0** |
| runninghub | L23 `get_by_text("2111762608670990337 · plus · 并发 5 · 已校验版本")` | **0** |
| runninghub | L24 `get_by_role("checkbox", name="TEST_RENDER_HUMAN / A · TEST_ren")` | **0** |
| runninghub | L25 `get_by_text("没有就绪图片。请先到资产管理上传服装图。")` | **0** |

**旧 DOM id 为何必然渲染不出来**（源级 + 产物级双重取证）：

```bash
git grep -c "alert-page" origin/master -- frontend/src/        # 0
grep -rl "alert-page" tts_erp_v2/static/frontend/           # 0（已构建前端产物）
git ls-tree --name-only origin/master tts_erp_v2/templates/pages/   # 目录不存在（P6 已删 Jinja 模板）
```

`#alert-page` / `#alert-status` / `#alert-rows` 是被 P6 删掉的 Jinja 模板的 DOM。模板没了、React 不渲染、构建产物里也搜不到 ⇒ **三条判据永远不可能命中**。

### 2.2 runninghub 那三条是"死代码 + 陈旧文案"

它们恒假，但**从未被触发**：`tests/browser/test_frontend_pilots.py:69-74` 对 runninghub 走自己的分支（不调 `wait_canned_page`）：

```python
if slug == "runninghub":
    page.get_by_text("plus · 并发 5 · 已校验版本", exact=True).wait_for()
    page.get_by_role("checkbox", name="TEST_RENDER_HUMAN · 模板 A · TEST_ren · 0 段", exact=True).wait_for()
    page.get_by_text("没有就绪图片。", exact=False).wait_for()
else:
    wait_canned_page(page, slug)
```

真实文案是 `plus · 并发 5 · 已校验版本`（**没有 id 前缀**）、checkbox 名是 `TEST_RENDER_HUMAN · 模板 A · TEST_ren · 0 段`（**分隔符与尾缀都不同**）。
⇒ helper 的 runninghub 分支是**上一版文案的残留**：一旦有人把 runninghub 改回走 helper，它会直接 30s 超时变红。

### 2.3 命中但不可靠的判据（1 条）

| slug | 判据 | 实测 | 问题 |
| --- | --- | --- | --- |
| spu-profit-deterioration | L45 `get_by_text("TEST_RENDER_COMPARE_0", exact=True)` | count=1 | 它等的是**夹具里的 SPU id 文案**，不是页面结构。fixture 的 spuId 一改名就 30s 超时；页面没渲染但夹具数据还在时会误判就绪 |

它为什么能命中：React 表格首格渲染 `<strong>{item.spuId}</strong>`（ComparisonTable.tsx:91），而夹具把 spuId 造成了 `TEST_RENDER_COMPARE_{i}`（`tests/browser/conftest.py:176`）。

### 2.4 其余 16 个 slug 全部命中（无同类问题）

| slug | 判据 | 实测 |
| --- | --- | --- |
| ad-daily / manual-costs / shops / enum-map / sync-jobs / users / intercept-configs / intercept-requests | `get_by_role("grid", name=…)` | 各自 count=1 |
| intercept-stats | grid + `.uplot canvas` | 1 / 1 |
| dashboard | region + 2 grid + 文案 | 1 / 1 / 1 / 1 |
| runtime-configs | grid + `TEST_RENDER_RUNTIME` | 1 / 2 |
| media-assets / digital-humans | 空态文案 | 1 / 1 |
| video-publish | region + 2 文案 + grid | 1 / 1 / 1 / 1 |
| spu-roi / focused-spus | `.tabulator-row` + `#data-freshness[data-source=ads]` | 12 / 1 |

### 2.5 告警页可用的真实 React 锚点（实测）

| 候选锚点 | 实测 count | 评价 |
| --- | --- | --- |
| `get_by_role("grid", name="SPU 两期盈利对比")` | **1** | ✅ 推荐：来自 `ComparisonTable.tsx:89` 的 `Table.Content aria-label`，是**结构锚点**，与夹具文案无关 |
| `table[aria-label="SPU 两期盈利对比"]` | 1 | 等价（HeroUI 渲染成 `<table>` 且带 role=grid） |
| `get_by_role("table", name="SPU 两期盈利对比")` | 0 | ❌ 可访问角色是 grid 不是 table |
| `p[role="status"]` | 1 | ⚠ 只适合当"应用已挂载"的弱信号：loading / blocked / 空态 / meta 都会渲染它，不能当"数据已就绪" |

---

## 3. 改法

### 3.1 helper 改成"契约表"驱动

把散落的 if/elif 收敛成一张 **slug → 判据列表** 的契约表（唯一事实源），每条判据用角色/属性锚点，不用夹具文案：

| slug | 建议判据（实测命中） |
| --- | --- |
| spu-profit-deterioration | `get_by_role("grid", name="SPU 两期盈利对比")`；空态场景另配 `get_by_text("当前条件无匹配 SPU，请调整范围或日期；不代表全店正常或无风险。")`（index.tsx:155） |
| runninghub | 修正为真实文案：`get_by_text("plus · 并发 5 · 已校验版本")` + `get_by_role("checkbox", name="TEST_RENDER_HUMAN · 模板 A · TEST_ren · 0 段")` + `get_by_text("没有就绪图片。")` |
| 其余 slug | 保持现状（实测全部命中），只是搬进契约表 |

同时**删掉** L18/L19/L20 三条旧 DOM 判据——它们对应已删除的 Jinja 模板，永不命中，留着只会让人以为"告警页有 legacy 兜底分支"。

### 3.2 `TEST_RENDER_COMPARE_0` 从"就绪信号"降级为"数据断言"

它本来就是**数据契约**（SPU id 的形状），不该承担就绪职责：就绪交给结构锚点，`TEST_RENDER_COMPARE_` 的断言留在测试里。

### 3.3 影响面（git grep 全量核对）

**要改的文件**

| 文件 | 改什么 |
| --- | --- |
| `tests/support/page_readiness.py` | 契约表化；删 3 条旧 DOM 判据；修 runninghub 3 条文案；告警页换结构锚点 |
| `tests/browser/test_render_smoke.py` | 只调用方，**大概率无需改**（4 处调用：L53/61/74/82） |
| `tests/browser/test_frontend_pilots.py` | 1 处调用（L74）；runninghub 走自己的分支，**无需改** |
| `tests/scripts/test_browser_runtime.py` | 3 处引用（L74/88/93，用 "shops"），**无需改** |
| `tests/support/test_page_readiness_contract.py` | **新增**：契约表护栏（见 §4） |

**依赖 `TEST_RENDER_COMPARE_` 夹具约定的既有测试**（它们是**数据契约**不是就绪判据，改结构锚点后不受影响；但若要让 spuId 自由化，需同步这 3 处）

| 位置 | 内容 |
| --- | --- |
| `tests/browser/conftest.py:176` | 造 `spuId = TEST_RENDER_COMPARE_{i}` |
| `tests/browser/conftest.py:197` | spu-roi 的 `spu_ids=TEST_RENDER_COMPARE_{i}` 查询串 |
| `tests/browser/test_frontend_pilots.py:78` | 告警页自己等 `TEST_RENDER_COMPARE_0` |
| `tests/browser/test_spu_deterioration_alert_page.py:1011` | 断言容器含 `TEST_RENDER_COMPARE_` |

---

## 4. 护栏设计 + 破坏性自验证

### 4.1 护栏（`tests/support/test_page_readiness_contract.py`，新增）

| 护栏 | 断言什么 | 怎么被破坏时必须红 |
| --- | --- | --- |
| G1 覆盖完整性 | 契约表覆盖 helper 声明支持的全部 slug | 从契约表删掉某 slug → 必须红（否则删判据 = 静默失去保护） |
| G2 禁止旧 DOM | helper 实现体里不得再出现 `#alert-page` / `#alert-status` / `#alert-rows` | 加回一条旧 DOM 判据 → 必须红。**计数要锚到 symbol 实现体行、剔除 docstring 与字符串字面量**，否则 docstring 里提一句就满足判据 |
| G3 浏览器级契约 | 每个 slug 打开页面，断言契约表里的锚点在短超时内命中 | 把某条锚点写错（例如改成 `name="不存在的网格"`）→ 必须红 |
| G4 双向验证 | 落地转绿 **且** 实现移除要转红 | 同上 G3：改坏锚点必须红 |

G3 需要浏览器，属 `domain_browser` + `requires_browser`，与现有浏览器套件同节奏；G1/G2 是纯源级断言，可进 `fast`。

### 4.2 破坏性自验证（已实测，本轮已还原）

做法：把夹具的 spuId 从 `TEST_RENDER_COMPARE_{i}` 改成 `TEST_RENDER_BROKEN_{i}`（破坏就绪判据依赖的文案），跑依赖该判据的浏览器用例。

```bash
# 备份后临时改夹具
cp tests/browser/conftest.py /tmp/conftest.py.bak
# ... 把 "spuId": f"TEST_RENDER_COMPARE_{i}" 改成 f"TEST_RENDER_BROKEN_{i}"
bash scripts/test_isolated.sh fast tests/browser/test_render_smoke.py -k deterioration
```

真实输出：

```
BREAK_EXIT=1
FAILED tests/browser/test_render_smoke.py::test_page_has_no_horizontal_overflow[spu-profit-deterioration]
FAILED tests/browser/test_render_smoke.py::test_spu_deterioration_alert_has_no_horizontal_overflow_at_any_viewport[390]
FAILED tests/browser/test_render_smoke.py::test_spu_deterioration_alert_has_no_horizontal_overflow_at_any_viewport[768]
FAILED tests/browser/test_render_smoke.py::test_spu_deterioration_alert_has_no_horizontal_overflow_at_any_viewport[1280]
FAILED tests/browser/test_render_smoke.py::test_spu_deterioration_alert_has_no_horizontal_overflow_at_any_viewport[1920]
FAILED tests/browser/test_render_smoke.py::test_page_font_stacks_come_only_from_tokens[spu-profit-deterioration]

E  playwright._impl._errors.TimeoutError: Locator.wait_for: Timeout 30000ms exceeded.
E    - waiting for get_by_text("TEST_RENDER_COMPARE_0", exact=True) to be visible
```

结论：**就绪等待确实只挂在夹具文案上**，一旦改名就是 30s 超时（红），而不是"页面没渲染"这类可读错误。这条风险是真实的、可复现的。

**还原确认**（字节级）：

```
恢复后 sha256=f159d1e28b242f9a83f3ff72fcbe2fcb4bbc1fb0d73780ee44606eb772b698d1
OK 文件已完全还原
```

临时探针已删除；`git status` 中 `tests/browser/conftest.py` 无残留（仅剩其他 lane 的 `plugins/*` 子模块指针与 `node_modules/.vite` 缓存）。

---

## 5. 写域冲突分析

在跑分支：`origin/test/spu-page-browser-gate`（相对 master 改 4 个文件）

```
M scripts/_test_selection.py
M tests/e2e/test_spu_deterioration_alert_smoke.py
M tests/scripts/test_test_selection.py
M tests/support/test_runtime.py
```

| 本方案要碰的文件 | 是否在对方改动清单里 |
| --- | --- |
| `tests/support/page_readiness.py` | 否 |
| `tests/browser/test_render_smoke.py` | 否 |
| `tests/browser/test_frontend_pilots.py` | 否 |
| `tests/scripts/test_browser_runtime.py` | 否 |
| `tests/browser/conftest.py` | 否 |
| `tests/support/test_page_readiness_contract.py`（新增） | 否 |

⇒ **无写域冲突**。两点提醒：

1. 对方也动了 `tests/support/test_runtime.py`（把前端构建触发条件从 `tests/browser/` 扩到 `tests/browser/` + `tests/e2e/`）。helper 从那里 import `record_phase`，**该函数签名未变**（`record_phase(name: str, seconds: float) -> None`）⇒ 不构成耦合破坏。
2. 护栏文件建议放 `tests/support/`，**不要放 `tests/scripts/`**——对方已占用 `tests/scripts/test_test_selection.py`。

---

## 6. 实测命令清单（全部真实执行）

```bash
# 取证：helper 全文、调用方、夹具约定、在跑分支改动面
git show origin/master:tests/support/page_readiness.py | cat -n
git grep -n "page_readiness\|wait_canned_page" origin/master -- tests/ tts_erp_v2/ scripts/
git grep -rn "TEST_RENDER_COMPARE" origin/master -- tests/
git diff --name-status origin/master...origin/test/spu-page-browser-gate

# 旧 DOM id 的源级/产物级取证
git grep -c "alert-page" origin/master -- frontend/src/          # 0
grep -rl "alert-page" tts_erp_v2/static/frontend/              # 0
git ls-tree --name-only origin/master tts_erp_v2/templates/pages/ # 目录不存在

# 运行时逐条判定（临时探针，已删除）
bash scripts/test_isolated.sh fast tests/browser/tmp_probe_readiness.py -s

# 破坏性自验证（夹具已字节级还原）
bash scripts/test_isolated.sh fast tests/browser/test_render_smoke.py -k deterioration
```

## 7. 未执行 / 待确认

- **未做**：任何代码改动、提交、分支创建。本轮只出方案与实测。
- **未验证**：新契约表在**真实数据**（非夹具）下的表现——浏览器套件用夹具，真实页面的就绪时序未测。
- **待 Chief 确认**：第三轮实施时的写域（确认 `test/spu-page-browser-gate` 已合入 master 后再动手），以及 `runninghub` 分支是"删掉 helper 里的死分支"还是"修成真实文案并启用"——后者需要 runninghub 页面确实提供该文案（实测页面上有 `plus · 并发 5 · 已校验版本`，但 pilots 用的那套文案来自夹具）。

---

## 8. 实施记录（Chief 批准后执行，2026-11-10）

Chief 的决定：① runninghub 死分支**删掉，本轮不启用**；② 改法照方案执行（契约表 + grid 角色锚点 + `TEST_RENDER_COMPARE_0` 降级为数据断言）；③ 第三轮合完再动手；④ 护栏 G2 剔除 docstring、G3 逐 slug 真实命中、G4 双向验证，并补一条**真实数据下的就绪时序**验收（夹具全绿不算数）；⑤ 方案文档随分支一起交。

本次落地：

| 文件 | 改动 |
| --- | --- |
| `tests/support/page_readiness.py` | 契约表化：`Anchor` 数据类 + `CONTRACTS`（17 个 slug）+ 显式 `SUPPORTED_SLUGS`；删 3 条旧 DOM 判据与 runninghub 死分支；告警页改用 `get_by_role("grid", name="SPU 两期盈利对比")`；未知 slug 直接 `AssertionError`，不再静默等一个永不出现的元素 |
| `tests/support/test_page_readiness_contract.py` | 新增：G1 覆盖完整性、G2 禁止旧 DOM、G3 锚点必须存在于 React 源码，外加三条破坏性自验证 |
| `tests/browser/test_page_readiness_contract.py` | 新增：浏览器级 G3 —— 每个 slug 打开夹具页面，锚点必须真的命中 |
| `tests/e2e/test_spu_deterioration_alert_smoke.py` | 补一条验收断言：`get_by_role("grid", name="SPU 两期盈利对比")` 必须在**真实 API + 真实 React 产物**的页面上命中（Chief 验收口径：夹具路径全绿不算数，夹具就是这次假绿的根源） |

**与方案的一处偏离（有意为之）**：方案 §4.1 的 G2 写的是"剔除 docstring 与字符串字面量"。docstring 剔除是对的（历史说明可以写），但**字符串字面量不能剔除**——退役 id 在代码里只可能以选择器字符串出现，剔除字面量等于让这条护栏永远无法触发（违背 G4"改坏必须红"）。实现口径：**只剔除 docstring，其余字符串字面量照查**。

**护栏文件落位**：G1/G2/G3(源码级) 放 `tests/support/`（进 fast 范围）；浏览器级 G3 放 `tests/browser/`（浏览器夹具只在 `tests/browser/` 下可用），与方案 §4.1 的分层一致。
