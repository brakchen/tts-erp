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
- `tests/browser/conftest.py`：3 处 → React renderer
- `scripts/oneoff_p6_collapse_migrated_routes.py`：一次性转换脚本（逐条断言片段唯一存在）
