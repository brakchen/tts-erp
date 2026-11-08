# P6 旧资源退役：测试侧改造清单

> 建立时间：2026-11-08 · Chief
> 依据：2026-11-08 实测统计，`tests/` 下共 **19 个文件、39 处**引用旧模板或旧脚本。
> 配套：`docs/handoff/p6-retirement-parallel-plan.md`（写域切分）

---

## 一、为什么测试侧必须一起改

旧模板和旧脚本被测试**直接读源文件内容做断言**，不是通过 HTTP 渲染。
删掉文件会让这些测试直接失败（`FileNotFoundError`），所以退役必须同时处理测试侧。

19 个文件分三类：

| 类型 | 判定标准 | 处置 |
| --- | --- | --- |
| **A 旧实现守卫** | 断言内容只对旧代码成立；注释明确写着 "legacy" / "retained legacy assets" / "not-yet-replaced runtime" | **删除该测试函数** |
| **B 渲染依赖** | 测试代码 import 或调用了 `_render_page` / 旧模板 | **改写为 React renderer 等价物** |
| **C 仍有效契约** | 约束的是接口、路由、静态资源可达性等与旧实现无关的行为 | **保留，只调整对旧文件路径的引用** |

---

## 二、逐文件处置

### A 类：旧实现守卫（删除测试函数）

| 文件 | 处数 | 说明 |
| --- | --- | --- |
| `tests/api/test_pages.py` | 12 | 主体是 `_render_page` 渲染旧模板后断言样式表顺序、sidebar CSS 注入位置。注释已写明 "Retained legacy assets still support the not-yet-replaced runtime"。**函数级删除**，保留文件里不依赖旧模板的测试 |
| `tests/api/test_manual_costs_page_v2.py` | 3 | 用 `_render_page` 渲染 manual-costs 旧模板断言含 `console.js` 与 bootstrap CSS |
| `tests/api/test_ad_daily.py` | 2 | 断言 ad-daily 旧页面响应体含 `static/js/ad-daily.js` |
| `tests/frontend/test_ad_daily_legacy.py` | 3 | 整个文件读 `static/js/dashboard.js` 与 `static/js/ad-daily.js` 断言实现细节。**React 版已有行为测试覆盖**，文件可整体退役 |
| `tests/frontend/test_source_contracts.py` | 1 | 读 `static/js/shops.js` 做源码契约断言 |
| `tests/publishing/test_contracts.py` | 6 | 读 `video-publish.html` / `video-publish.js` 断言文案与实现细节 |
| `tests/publishing/test_final_v12_findings.py` | 2 | 同上 |
| `tests/publishing/test_final_v14_findings.py` | 1 | 同上 |
| `tests/publishing/test_final_v16_findings.py` | 2 | 同上 |
| `tests/publishing/test_final_v18_findings.py` | 1 | 同上 |
| `tests/publishing/test_final_v20_findings.py` | 1 | 同上 |
| `tests/publishing/test_polling_channels.py` | 1 | 读 `video-publish.js` 断言轮询通道 |
| `tests/publishing/test_upload_cancellation.py` | 1 | 读 `video-publish.js` |
| `tests/publishing/test_video_publish_frontend.py` | 3 | 读 `video-publish.js` |

**video-publish 的等价覆盖（已核实）**：`frontend/src/pages/video-publish/index.test.tsx` 有 700+ 行**行为级测试**，
覆盖 `clientRequestId` 幂等、`If-None-Match` 304 处理、`AbortController` 取消、上传取消、重复提交防护等——
比旧版的源码字符串断言更强。删除旧断言不丢覆盖。

### B 类：渲染依赖（改写）

| 文件 | 处数 | 改法 |
| --- | --- | --- |
| `tests/browser/conftest.py` | 3 | **已由 Chief 完成**：`_render_pages()` 里最后一处 `_render_page(f"{_ALERT_SLUG}.html", ...)` 改为 `frontend_pages.render_frontend_page(request, _ALERT_SLUG)`；未迁移页面的 else 分支改为显式 `AssertionError`（所有页面都已迁移，该分支本就不可达） |

### C 类：仍有效契约（保留并微调）

| 文件 | 处数 | 说明 |
| --- | --- | --- |
| `tests/api/test_middleware.py` | 2 | 断言 `/static/js/console.js` 静态可达。**console.js 是否退役取决于 lane A 的判断**，若保留则此处不动 |
| `tests/api/test_frontend_pages.py` | 1 | 检查 React 页面不再引用 `static/js/`。**这条正好是退役后的正向断言，保留** |
| `tests/api/test_frontend_remaining_pages.py` | 2 | 断言响应体不含 `static/js/<page>.js`。**退役后应保留，且可扩展为断言不含任何旧脚本** |
| `tests/browser/test_frontend_pilots.py` | 1 | 断言 React 页不加载 `/static/js/`。**保留** |
| `tests/frontend/test_style_tokens.py` | 1 | 用 tinycss2 校验 `tokens.css` 令牌（第 222 行 glob 了 `static/js/*.js`）。**主体保留**，只需把 glob 范围收窄或改为校验 React 源码 |

---

## 三、执行顺序

1. **先删 A 类**，再删旧模板/旧脚本——顺序反了会得到一堆 `FileNotFoundError` 而不是真正的断言失败
2. **再改 B 类**（conftest 已完成）
3. **C 类微调**
4. **最后删文件**：`tts_erp_v2/templates/pages/*.html`（12 个）、`tts_erp_v2/static/js/*.js`（除 console.js 外 12 个）
5. **删 `_render_page` 辅助函数**（`tts_erp_v2/api/v2/pages.py`），以及随之无用的 Jinja 环境初始化

---

## 四、验收

```bash
# 1. 无残留引用
git grep -n "templates/pages/\|static/js/\|_render_page" -- tests/ tts_erp_v2/

# 2. 隔离测试
bash scripts/test_isolated.sh fast

# 3. DoD 基线
bash scripts/test_baseline.sh --scope auto --base "$target_sha"
```

**注意测试机资源**：HP desk 只有 4 核，同时跑两个 pytest 会让负载到 7 以上，
浏览器测试会因资源竞争出现假失败（2026-11-08 实测：`test_frontend_components`
在并发下失败、单独跑 `[100%]` 通过）。**同一时刻只跑一个全量套件。**

---

## 五、已完成的改动（Chief，2026-11-08）

- `tts_erp_v2/api/v2/pages.py`：14 处双路分支 → 单一 `render_frontend_page`
- `tts_erp_v2/api/v2/ad_daily.py`：1 处（该页旧 HTML 内联在 Python）
- `tests/browser/conftest.py`：3 处 → React renderer；另修复多目录 `conftest` basename 冲突
- `tests/browser/test_render_smoke.py`：告警页就绪信号改用共享 `wait_canned_page`
- `tests/render_support.py`：字体白名单不再要求三套栈互不相同
- `tests/frontend/test_style_tokens.py`：检查对象迁到 React 样式源；区分设计令牌与组件作用域令牌
- `tts_erp_v2/static/css/tokens.css`：补齐未声明的 `--surface-secondary`
- `scripts/oneoff_p6_collapse_migrated_routes.py`：一次性转换脚本（逐条断言片段唯一存在）

**测试侧清理**（分支 `feature/p6-retire-legacy-assets`）：21 个文件，+200/-1974。
A 类旧实现断言已删，C 类正向断言已保留并扩展为「不含任何已退役脚本名」
（名单集中在 `tests/support/retired_frontend_assets.py`）。

---

## 六、另一件没做的事：旧 CSS 尚未清理

`tts_erp_v2/static/css/` 下仍有 7 个文件，其中 5 个是旧页面专用的：
`ad-daily.css`、`runtime-configs.css`、`sync-jobs.css`、
`spu-profit-deterioration.css`、`video-publish.css`。

**核实结论（2026-11-08）**：

| 文件 | 生产代码引用 | 测试引用 | 处置 |
| --- | --- | --- | --- |
| `video-publish.css` | 无 | **有活契约测试**：`tests/publishing/test_contracts.py` 直接读文件内容断言 `max-width:1440px/900px/390px` 等；`scripts/_test_selection.py:209` 把它登记为活资产 | **必须保留** |
| `ad-daily.css` | 无（内联 HTML 已删） | 仅正向断言「响应不含它」 | 可删 |
| `runtime-configs.css` | 无 | 仅正向断言 | 可删 |
| `spu-profit-deterioration.css` | 无 | 仅正向断言 | 可删 |
| `sync-jobs.css` | 无 | 无 | 可删 |

**结论**：4 个可删、1 个必须留。但旧 CSS 的退役是一个**独立任务**，
需要逐个确认契约测试是否还有效、React 侧是否已有等价实现。
这超出了 P6 的既定范围（方案只覆盖模板与脚本），本轮不动。

> 顺带一提：`console.js` 与 `static/vendor/` 同样必须保留——
> `console.js` 仍挂在 `app.py:149` 的 public static mount 上，
> `vendor/tabulator.*` 被 `frontend/src/pages/spu-profitability/ProfitabilityTable.tsx` 直接 import，
> `vendor/bootstrap.min.css` 被 `auth.py:305` 的回落登录页引用。
> **P6 的退出条件「消费者清单为空」对这两处不成立**，不是遗漏。

---

## 六、还剩什么：删文件

| 类型 | 文件 | 数量 |
| --- | --- | --- |
| 旧 Jinja 模板 | `tts_erp_v2/templates/pages/*.html` | 12 |
| 旧页面脚本 | `tts_erp_v2/static/js/*.js`（**除 `console.js`**） | 12 |
| `_render_page` 辅助函数 + Jinja 环境初始化 | `tts_erp_v2/api/v2/pages.py` | — |

**`console.js` 本轮不删**：它挂在 `app.py` 的 public static mount 上，是公共静态资源而非页面资产，
`tests/api/test_middleware.py` 仍在断言它可达。

**执行顺序**：测试侧清理已完成 → 可以删文件了。删完必须跑一次 `fast` 全套确认零失败。

**删除前的强制检查**（每个文件都要过）：
```bash
git grep -n "dashboard.html" -- tts_erp_v2/ tests/
git grep -n "dashboard.js" -- tts_erp_v2/ tests/
```

---

## 七、本轮发现的真实缺陷（不属于 P6，但被 P6 的断言扩展暴露）

**`--surface-secondary` 未声明**（`c2089bb` 引入）：
`frontend/src/pages/media-assets/media-workspace.css` 的第 70、104 行引用了 `var(--surface-secondary)`，
但全仓没有任何声明——浏览器会把它解析成无效值，这两个元素的背景色实际是透明的。
全仓扫描确认这是**唯一**一个未声明的 CSS 令牌。已在 `tokens.css` 的 surface 组补
`--surface-secondary: #E9EEE7`（与既有的 `--default` 同色）。

**教训**：旧断言只检查旧模板，React 侧的 CSS 契约从未被覆盖过。
