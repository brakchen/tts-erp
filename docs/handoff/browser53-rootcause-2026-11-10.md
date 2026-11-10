# browser53 根因定位：`test_spu_deterioration_alert_page.py` 的 53 项失败（2026-11-10）

> 执行人：测试工程师（bot_d330a0b9a748）｜委托：Chief
> 测试机：HP desk `schan@192.168.1.2`（`ssh hp-desk`）
> 本轮**只定位与结论，不改任何断言**（未改断言 / 未调长等待 / 未 skip / 未放宽业务事实断言）
> 独立 worktree：`/home/schan/tts-erp/.worktrees/browser53-20261110`（`origin/master` @ `e3e85e0e`）

---

## 0. 结论速览

**这 53 项等到的只有两个 locator，两个都是已退役 Jinja 模板的元素 id，React 页面从不渲染它们。**

| # | 等不到的 locator | 失败项数 | 来源 | 超时 |
| --- | --- | --- | --- | --- |
| 1 | `#alert-rows [data-spu-pk='1001']` | **22** | 测试自带的 `ComparisonSite.ready()`（旧文件 L171） | 5000ms |
| 2 | `#alert-status`（断言其 `data-kind` ∈ error/loading/ready） | **31** | 旧文件 L258 / L281 / L467 / L525 / L566 / L621 / L771 | 5000ms |

22 + 31 = **53**，与失败总数精确对上，没有第三类。

**并且：页面本身渲染是正常的。** 同一份报错里 Playwright 附带的 Aria 快照显示
`form "对比范围"`、`region "已应用对比范围"`、`region "完整分析范围统计"`、
`heading "完整分析范围统计"`、`term: 分析 SPU 数`、`grid "SPU 两期盈利对比"`
**全部存在**。

⇒ **不是「接口没给出预期状态」，也不是「页面状态元素不出现」；
是「测试等的元素在 React DOM 里从来就不存在」。**
委托描述里「失败机制是页面状态元素不出现」这一判断需要更正：页面状态是有的，
React 用 `p[role="status"]` + 中文文案表达状态，测试却去找旧模板的 `#alert-status[data-kind=...]`。

⇒ **master 上这个文件已经按 React 契约改写过了**（见 §5），这 53 项是**改写前版本**的失败数。

---

## 1. 取证

### 1.1 报错原文（真实运行输出）

来源：2026-11-09/10 在 `52929fa0`（旧文件版本）上跑的完整 fast 套件，
原始日志 `/tmp/TEST_baseline_fast_master3.log`。

```
E       AssertionError: Locator expected to have attribute 'error'
E       Actual value: None
E       Error: element(s) not found
E       Call log:
E         - Expect "to_have_attribute" locator("#alert-status") with timeout 5000ms
E         - waiting for locator("#alert-status")
E
E       Aria snapshot:
E       - complementary:
E         - text: tts / erp
E         - navigation "主导航":
E           - heading "总览" [level=2]
...
```

第二类（`site.ready()`）：

```
tests/browser/test_spu_deterioration_alert_page.py:171: AssertionError
E       AssertionError: Locator expected to have count '1'
E       Actual value: 0
E       Error: element(s) not found
E       Call log:
E         - Expect "to_have_count" locator("#alert-rows [data-spu-pk='1001']") with timeout 5000ms
E         - waiting for locator("#alert-rows [data-spu-pk='1001']")
```

### 1.2 失败项的源码行分布（真实日志统计）

按该文件的失败块统计「失败源码行 → 等不到的 locator」：

```
   4  L371  -> #alert-rows [data-spu-pk='1001']
   4  L478  -> #alert-rows [data-spu-pk='1001']
   3  L406  -> #alert-rows [data-spu-pk='1001']
   3  L705  -> #alert-rows [data-spu-pk='1001']
   3  L793  -> #alert-rows [data-spu-pk='1001']
   1  L184  -> #alert-rows [data-spu-pk='1001']
   1  L213  -> #alert-rows [data-spu-pk='1001']
   1  L297  -> #alert-rows [data-spu-pk='1001']
   1  L452  -> #alert-rows [data-spu-pk='1001']
   1  L634  -> #alert-rows [data-spu-pk='1001']
   （另 31 项落在 L258/L281/L467/L525/L566/L621/L771，等的是 #alert-status）

超时时长分布：22 × 5000ms（Playwright expect 默认超时，不是 30s）
```

旧文件里对应的源码：

```
L171  expect(self.page.locator("#alert-rows [data-spu-pk='1001']")).to_have_count(1)   # site.ready()
L258  expect(page.locator("#alert-status")).to_have_attribute("data-kind", "error")
L525  expect(page.locator("#alert-status")).to_have_attribute("data-kind", "loading")
L771  expect(page.locator("#alert-status")).to_have_attribute("data-kind", "ready")
```

### 1.3 master 上的复跑：全绿

```
$ cd /home/schan/tts-erp/.worktrees/browser53-20261110
$ git rev-parse --short HEAD
e3e85e0e
$ bash scripts/test_isolated.sh browser tests/browser/test_spu_deterioration_alert_page.py
EXIT=0 ELAPSED=79s

[isolated-test] cloning tts_erp_test_template -> tts_erp_test__schan_20261110041227_1239243_cad72176
[isolated-test] running: bash scripts/test.sh browser tests/browser/test_spu_deterioration_alert_page.py
.....................................................                    [100%]
[test-timing] pytest_total=73.195s
[test-timing] isolated_total=79.188s
[isolated-test] dropping ephemeral DB: tts_erp_test__schan_20261110041227_1239243_cad72176
```

**18 个测试函数（参数化后更多项）全部通过，0 失败。**

⇒ **这 53 项是「改写前版本」的失败数。master 上该文件已经按 React 契约改写完毕（§5 选项 A）。**
本轮**没有发现任何生产代码缺陷**：React 页面渲染正确、canned 数据到位、
共享就绪契约在 React 页面上走得通；失败的只是旧断言指向了已退役的 DOM。

复跑纪律：跑前 `ps -eo args | grep -c "[p]ytest"` 确认为 0（当时确有另一 lane 在跑，
等空窗后 12:12:27 开跑），全程同一时刻只跑一个套件。


---

## 2. 页面渲染了什么（夹具侧）

`tests/browser/conftest.py::_render_pages` 对告警页的处理（`origin/master`）：

```python
_ALERT_SLUG = "spu-profit-deterioration"
...
for prefix in ("", "/tts"):
    request = Request(
        {"type": "http", "root_path": prefix, "path": f"/v2/pages/{_ALERT_SLUG}",
         "access_grant": AccessGrant(mode=AuthMode.ENFORCE, role=Role.ADMIN)}
    )
    response = frontend_pages.render_frontend_page(request, _ALERT_SLUG)
    name = ("tts-" if prefix else "") + _ALERT_SLUG
    (out / f"{name}.html").write_bytes(response.body)
```

⇒ 夹具给浏览器的是 **React SPA 外壳**，**DOM 由客户端 JS 运行时产生**，不是服务端渲染的成品页。
这正是「旧 id 在 DOM 里不存在」的结构性原因：服务端不再渲染 Jinja 模板，浏览器里就没有那些 id。

数据来自同文件的 `_alert_payload()`（canned 数据）：

```python
"spuPk": 1000 + i,
"spuId": f"TEST_RENDER_COMPARE_{i}",
"previousOrderCount": 0 if i == 2 else 18,
"currentOrderCount": 0 if i == 2 else 16,
```

### 2.1 就绪等待为什么能走通

共享就绪契约 `tests/support/page_readiness.py::wait_canned_page` 的告警页分支：

```python
if slug == "spu-profit-deterioration" and page.locator("#alert-page").count():
    page.locator('#alert-status[data-kind="ready"]').wait_for()      # 死代码：#alert-page 永不渲染
    page.locator('#alert-rows [data-spu-pk]').first.wait_for(state="attached")
...
elif slug == "spu-profit-deterioration":
    page.get_by_text("TEST_RENDER_COMPARE_0", exact=True).wait_for()  # ← React 实际走这条
```

`#alert-page` 是旧模板的根 id，React 不渲染 ⇒ 条件恒假 ⇒ 落到 fallback 分支，
等 `TEST_RENDER_COMPARE_0`。而 React 的表格首格渲染
`<strong className="break-all">{item.spuId}</strong>`（`ComparisonTable.tsx:91`），
`item.spuId` 正是 `TEST_RENDER_COMPARE_{i}` ⇒ **这个等待在 React 页面上是成功的**。

⇒ 也就是说：**页面就绪、表格渲染、统计区渲染都正常，只有旧断言在等不存在的元素。**

---

## 3. 对照表：旧断言 → React 侧真实存在的元素

| 旧断言（等不到） | React 侧真实元素 | 出处 |
| --- | --- | --- |
| `#alert-rows [data-spu-pk='1001']` | `table[aria-label="SPU 两期盈利对比"] tbody tr:has([aria-controls="detail-1001"])` | `ComparisonTable.tsx:88-92`（行用 `aria-controls="detail-<spuPk>"` 标识，**没有 `data-spu-pk` 属性**） |
| `#alert-rows tr` | 同上（`{TABLE} tbody tr`） | `ComparisonTable.tsx:89` |
| 行内 `[data-value="previousRoi"]` 等 8 个键 | Metric 块：`<strong>标签</strong>` + `<div>上期 <span>值</span></div>` / `<div>本期 <span>值</span></div>`；差异在 `<p>展示差额 …</p>` / `<p>展示变化率 …</p>` | `ComparisonTable.tsx:20-22` |
| `#total-spus`（文本 12 / —） | `section[aria-label="完整分析范围统计"] dl > div` 中 dt=`分析 SPU 数` / `本期订单数` / `上期订单数` 的 dd | `index.tsx:150-151` |
| `#alert-status[data-kind="ready"]` | `section[aria-label="已应用对比范围"]` 内的 `p[role="status"]`（`数据未更新 · 实际数据日期：…；计算时间：…`） | `index.tsx:141-146` |
| `#alert-status[data-kind="loading"]` | `p[role="status"]` 文本「正在加载当前范围…」 | `index.tsx:138` |
| `#alert-status[data-kind="error"]` | **没有单一 status 元素**：blocked 时是 `p[role="status"]`「服务端等待期内，读取暂不可用…」；HTTP 错误是 `readFailure()` 的中文文案（`参数或日期错误，请检查输入（HTTP 422）` 等） | `index.tsx:12-15, 137` |
| `#filter-shop` | `form[aria-label="对比范围"]` 内的 HeroUI select（`role="button"`，name 匹配「活动范围」） | `index.tsx:125` |
| `#filter-anchor-date` | `input[aria-label="截至日（店铺当地日）"]` | `index.tsx` 日期输入 |
| `#alert-summary` | 明细区 `section[role="region"][aria-labelledby^="detail-title-"]`（`id="detail-<spuPk>"`） | `ComparisonTable.tsx:57-58` |
| `#alert-cards` | 同一张表（窄屏是卡片式呈现，但 DOM 仍是同一张表） | `ComparisonTable.tsx:88-89` |
| `#alert-page`（页面根） | 无此 id；页面根是 `.erp-page` | `index.tsx:120` |
| — | 表格滚动区：`[role="region"][aria-label="SPU 对比表格滚动区域"]` | `ComparisonTable.tsx:88` |
| — | 主导航：`nav[aria-label="主导航"]`，告警页入口 `a[href$="/v2/pages/spu-profit-deterioration"]`（文本「利润劣化告警」） | `index.tsx` / 布局 |
| — | 来源覆盖明细：`section[aria-label="来源 <name>"]`，内含 `<strong>advertiserId / campaignId</strong>` 与 `<p>观察日期：…</p>` | `CoverageDetails.tsx:7,16` |

**页面就绪契约六项**（master 改写后的测试文件用的就是这一组）：

```python
TABLE   = 'table[aria-label="SPU 两期盈利对比"]'
ROWS    = f"{TABLE} tbody tr"
SCROLL  = '[role="region"][aria-label="SPU 对比表格滚动区域"]'
SCOPE   = 'section[aria-label="已应用对比范围"]'
STATS   = 'section[aria-label="完整分析范围统计"]'
FILTERS = 'form[aria-label="对比范围"]'
NAV     = 'nav[aria-label="主导航"]'
```

---

## 4. 结论：为什么等不到

1. **P6 退役了服务端的 Jinja 模板**（`tts_erp_v2/templates/pages/spu-profit-deterioration.html`，提交 `f72a3089`），
   页面改由 React 拥有。**React 从来没有渲染过 `#alert-rows` / `#alert-status` / `#total-spus` / `#filter-shop` / `[data-value=...]`。**
2. 夹具渲染的是 React SPA 外壳，DOM 在客户端由 JS 产生，**这些 id 在 DOM 里不存在**。
3. Playwright 的 `expect()` 对不存在的 locator 会**等满默认超时 5000ms**，然后报
   `element(s) not found` / `Actual value: None` —— 这就是「等不到」的全部机制。
4. 与此同时**页面本身是好的**：同一份报错附带的 Aria 快照里，React 的
   `form "对比范围"`、`region "已应用对比范围"`、`region "完整分析范围统计"`、`grid "SPU 两期盈利对比"`
   都在，数据也到了（统计区有 `term: 分析 SPU 数`）。
5. ⇒ **不是接口失败、不是数据缺失、不是超时太短、不是并发。**是断言指向了已经不存在的 DOM。

---

## 5. 修法选项

> 本轮**一个都没实施**（委托明确要求只出结论）。

### 选项 A —— 把断言改写为 React 契约（**master 上已完成**）

`origin/master` 的该文件已经这样改写（相对旧版 **720 行改动，231 插入 / 495 删除**）：

```
$ git diff --stat origin/master b92c1e4b -- tests/browser/test_spu_deterioration_alert_page.py
 tests/browser/test_spu_deterioration_alert_page.py | 720 ++++++--------------
```

master 版把六个契约常量、`METRIC_KEYS`（旧 `[data-value]` 键 → React 标签 + 期间列）、
`site.ready()`（改为 `_row()` 按 `aria-controls="detail-<spuPk>"` 定位行）全部换掉，
并在文件头写明「React 从未渲染 `#alert-rows` / `#total-spus` / `#alert-status`」。

- **动哪些文件**：`tests/browser/test_spu_deterioration_alert_page.py`（一个）
- **不动**：`frontend/`、业务事实断言
- 相关提交：`a08b187b`、`f3a99cb8`、`08c44603`

⇒ **如果目标只是让这 53 项变绿，master 已经做完了；本分支线落后，需要同步 master。**

### 选项 B —— 在落后于 master 的分支线上补同一份改写

- **动哪些文件**：同 A（一个文件）
- 做法：直接取 master 版本（但注意该文件依赖 `tests/support/page_readiness.py` 与
  `tests/browser/conftest.py` 的配套改动，需一起对齐）

### 选项 C —— 清理共享就绪契约里的死代码（可选）

`tests/support/page_readiness.py` 开头那段：

```python
if slug == "spu-profit-deterioration" and page.locator("#alert-page").count():
    page.locator('#alert-status[data-kind="ready"]').wait_for()
    page.locator('#alert-rows [data-spu-pk]').first.wait_for(state="attached")
```

React 接管后 `#alert-page` 永不渲染 ⇒ 条件恒假 ⇒ 这段**永远不执行**（死代码）。
留着会误导下一个人以为告警页还有 `#alert-status`。

- **动哪些文件**：`tests/support/page_readiness.py`（删 3 行或改成注释说明）
- **风险**：低（该分支恒不进入）；但删掉前要确认没有别的页面走这个 slug

### 选项 D —— 加一条「不得再出现退役 id」的静态护栏（可选，防回退）

- **动哪些文件**：新脚本（如 `scripts/check_retired_dom_ids.py`）+ CI 接线
- **判据**：在 `tests/browser/` 里 grep `#alert-rows|#alert-status|#total-spus|#filter-shop|data-spu-pk`
  命中即非零退出
- **价值**：这次的 53 项之所以能长期存在，正是因为没有任何护栏拦「测试引用已退役 DOM」

### 明确**不推荐**的选项

- ❌ 加长等待 / 放宽超时 —— 等的是不存在的元素，加多久都不会出现
- ❌ skip / xfail —— 会把「断言指向已退役 DOM」这类真实问题藏起来
- ❌ 改 React 侧去渲染这些旧 id —— 等于把已退役的 Jinja 契约请回来，与 P6 退役方向相反

---

## 6. 未执行 / 未覆盖

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 任何断言改动 | 未执行 | 委托明确要求本轮只出结论 |
| 在旧分支线复现 53 项 | 未单独复跑 | 报错原文取自 2026-11-09/10 的完整 fast 套件真实日志（同一文件旧版本），已含完整 Call log 与 Aria 快照；单独复跑需再占 25 分钟机器窗口 |
| `--scope fast` 全量对照 | 未执行 | 本轮是定位任务，不涉及改动，无需失败集比对 |
| 80 个存量浏览器失败的成因 | 不在本轮范围 | 其中 53 项属于本文件，其余分布在 `test_spu_price_columns.py` 等文件 |
| `tests/e2e/test_spu_deterioration_alert_smoke.py` 的 3 项 | 未执行 | 由 `test/spu-page-browser-gate` 分支处理，本轮不碰 |

---

## 7. 现场

| 路径 | 说明 |
| --- | --- |
| `/home/schan/tts-erp/.worktrees/browser53-20261110` | 本轮 worktree，`origin/master` @ `e3e85e0e` |
| `/tmp/TEST_browser53.log` | master 上的隔离实跑原始日志 |
| `/tmp/TEST_base3_clean.txt` | 旧基线日志的干净版（本轮的取证来源） |
| `/tmp/TEST_old_alert_page.py` | 旧版被测文件（旧分支线 `b92c1e4b`） |
| `/tmp/TEST_baseline_fast_master3.log` | 53 项报错原文的原始来源 |
