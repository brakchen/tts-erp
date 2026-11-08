# P6 退役与收口 —— 并行执行方案

> 建立时间：2026-11-08 · 总管：Chief
> **状态：已完成（2026-11-08）** —— 路由收敛、测试侧清理、旧资产删除均已落地并验证。
> 依据：`docs/plans/frontend-progressive-migration.md` §7（P6 批次定义）、§5.1（renderer 选择）、§6.3（发布产物）

---

## 一、为什么现在做 P6

前端渐进迁移的 P2–P5 **实质已经完成**，方案文档的状态说明已过时。实测依据：

| 事实 | 位置 |
| --- | --- |
| `MIGRATED_PAGE_IDS` 已含 19 个页面 | `tts_erp_v2/api/v2/frontend_pages.py` |
| `PAGES` 注册表共 19 个页面，全部已迁移 | `tts_erp_v2/accounts/pages.py:26` |
| 19 个页面目录都有真实 React/TS 代码，合计约 1.4 万行 | `frontend/src/pages/` |
| 12 个旧 Jinja 模板仍在 | `tts_erp_v2/templates/pages/` |
| 13 个旧 JS 脚本仍在 | `tts_erp_v2/static/js/` |
| 旧 renderer 分支仍在代码里 | `tts_erp_v2/api/v2/pages.py`（如 `:309` 的 `_render_page("shops.html", ...)`） |

**结论**：剩余工作就是 P6 —— 退役旧实现、清理无消费者资源、收口 renderer。

---

## 二、并行切分原则

P6 最适合并行，因为退役对象是**文件**，天然可以按页面组切成互不重叠的写域。

三条硬规则：

1. **一个文件只有一个 writer**。三个 lane 的文件清单不得有交集。
2. **共享文件只有一处能改**。见下方「共享资源」一节，由 lane A 独家负责。
3. **不改 `MIGRATED_PAGE_IDS` 内容**。它已经是 19 个页面全集，本次不增不减。

### 共享资源（只有 lane A 能碰）

| 文件 | 为什么共享 |
| --- | --- |
| `tts_erp_v2/api/v2/pages.py` | 所有页面的 renderer 路由都在这一个文件 |
| `tts_erp_v2/static/js/console.js` | 挂在 `app.py` 的 public static mount 上，多页面共用 |
| `tts_erp_v2/static/vendor/*` | 无消费者的 vendor 判断要全局看 |
| `frontend/src/app/*` | AppShell / navigation / page-loader 共享层 |

---

## 三、三个 lane 的写域

### 3.0 实测补充：退役还牵涉 19 个测试文件（2026-11-08 21:30 核实）

原方案只列了生产代码，**漏了测试侧**。实测：`tests/` 下有 **19 个文件、39 处**引用旧模板或旧脚本。

这些引用分两类，**不能一刀切删除**：

| 类型 | 例子 | 处置 |
| --- | --- | --- |
| **旧实现断言**（只对旧代码成立，React 版本已用行为测试覆盖） | `tests/api/test_pages.py:98` 读 `intercept-configs.html` 检查样式表顺序；`tests/publishing/test_contracts.py:262` 读 `video-publish.html` 检查文案 | 删除，或改写为对 React 组件的行为断言 |
| **仍有效的契约**（约束的是接口/路由行为，不是旧文件本身） | `tests/api/test_middleware.py:310` 检查 `/static/js/console.js` 静态可达；`tests/browser/conftest.py:448` 的 `_render_pages` 辅助 | 改写为对 React 页面的等价断言，或保留对应资产 |

`tests/browser/conftest.py` 的 `_render_pages()` 直接 import `_render_page` 并渲染旧模板给浏览器测试用——这是**渲染依赖**，删模板前必须先改它。

**已知等价覆盖**：video-publish 的旧 JS 源码字符串断言（幂等 `clientRequestId`、`If-None-Match`、`AbortController`、5 秒刷新节奏）在 `frontend/src/pages/video-publish/index.test.tsx` 已有 700+ 行**行为级测试**覆盖，比源码字符串断言更强。这类可以直接删旧断言。

### 3.1 lane A —— 退役路由与共享资源（由 lane A owner 写）

**独占写域：**
- `tts_erp_v2/api/v2/pages.py`
- `tts_erp_v2/static/js/console.js`
- `tts_erp_v2/static/vendor/`
- `frontend/src/app/`

**任务：**
1. 删除 `pages.py` 里每个已迁移页面的 `else: return _render_page("xxx.html", ...)` 分支，只保留 `MIGRATED_PAGE_IDS` 命中时的 React renderer
2. 同步删除已无意义的 `_render_page` 辅助函数（若确认没有非迁移页面还在用）
3. 判断 `console.js` 是否还有非页面消费者；若没有则删除，并同步处理 `app.py` 的 mount 与 `templates/pages/manual-costs.html` 的引用（后者归 lane B 删除，所以**先与 lane B 协调**：lane B 删除 manual-costs 模板后，lane A 再删 console.js 和 app.py 的相关 mount）
4. 清理无消费者的 `vendor/` 资源

**顺序约束：** 本 lane 的第 3 步依赖 lane B 删除 manual-costs 模板后开始。

**已有进展（Chief 于 2026-11-08 完成，待合并）**：

第 1 步已实现并落盘，用一次性脚本 `scripts/oneoff_p6_collapse_migrated_routes.py` 做精确片段替换
（该脚本逐条断言片段在源码中唯一存在，避免正则跨行匹配的脆弱性）：

- `tts_erp_v2/api/v2/pages.py`：14 处双路分支收敛为单一 `render_frontend_page(request, "<page>")`
- `tts_erp_v2/api/v2/ad_daily.py`：1 处（该页旧 HTML 内联在 Python 里，形态与其它页不同）
- 合计 15 处，`git diff --stat` 为 2 文件 +15 / -83 行
- 收敛后 `_render_page` 零调用，`MIGRATED_PAGE_IDS` 条件分支全部消失

**语义变化（重要）**：`MIGRATED_PAGE_IDS` 的含义从「哪些页面用 React / 其余回落旧 renderer」
收敛为「**哪些页面已迁移**」——不再兼任回落开关。依据：

- `accounts.pages.PAGES` 注册的 **18 个页面全部已迁移**（`login` 由 `auth.py` 的独立分支拥有，不在注册表里）
- 因此「未迁移页面走旧 renderer」这个能力**已无真实使用者**，旧 Jinja renderer 可以整体退役

**受影响的测试**：`tests/api/test_frontend_pages.py::test_non_migrated_route_keeps_legacy_renderer`
用 `activated` fixture 缩小迁移集合来模拟「未迁移」状态，收敛后该能力不存在，测试失败。
已替换为 `test_every_registered_page_is_migrated`：断言注册页面集合 ⊆ `MIGRATED_PAGE_IDS`，
且每个注册页面确实由 React renderer 渲染（响应含 `frontend-bootstrap`）。

**`MIGRATED_PAGE_IDS` 本身保留**，它仍是以下几处的唯一接线：
- `tts_erp_v2/api/v2/auth.py:130`（登录页分支）
- `tests/browser/conftest.py:589`（跳过未迁移页面的浏览器 fixture）
- 多个浏览器测试的成员断言

### 路由收敛引入的两处回归（已修）

路由收敛改变了实际 renderer，暴露出两处**依赖旧 DOM 的测试**。两处都是路由收敛的直接后果，不是 master 原有失败：

1. **字体栈断言**：`tests/render_support.py::token_font_stacks` 断言 `len(stacks) == 3`（三套栈必须互不相同）。
   但 `fa475e0e`（2026-11-08「统一 ERP 中文与数字字体栈」）是有意的产品决定——把 `--mono`/`--serif` 都指向 `--sans`。
   **是断言过时，不是代码有缺陷**。已改为「三个别名都必须声明且非空」，不再要求互不相同；
   白名单语义不变（三套栈相同时白名单是单元素集合）。

2. **告警页横向溢出用例**：`tests/browser/test_render_smoke.py` 硬编码等待旧模板的
   `#alert-status:not([data-kind='loading'])`。收敛后该页由 React 拥有，这个元素不存在，
   Playwright 等 10 秒超时 → 4 个视口全失败。已改为调用共享的 `wait_canned_page(page, ALERT_SLUG)`，
   它按 slug 分派到 React 版本的就绪信号（等 `TEST_RENDER_COMPARE_0` 文本）。

**教训**：切换 renderer 不只是改后端路由分支，还要检查**所有断言具体 DOM 结构的测试**——
这类测试的失败信息（Timeout 等不到某个 id）不会指向"renderer 换了"，很容易误判成环境问题。

### lane B —— 旧模板退役（基础设置组）

**独占写域：**
- `tts_erp_v2/templates/pages/dashboard.html`
- `tts_erp_v2/templates/pages/shops.html`
- `tts_erp_v2/templates/pages/enum-map.html`
- `tts_erp_v2/templates/pages/runtime-configs.html`
- `tts_erp_v2/templates/pages/sync-jobs.html`
- `tts_erp_v2/templates/pages/manual-costs.html`

**任务：** 逐个确认没有消费者后删除，同时删掉对应的旧脚本：
- `tts_erp_v2/static/js/dashboard.js`
- `tts_erp_v2/static/js/shops.js`
- `tts_erp_v2/static/js/enum-map.js`
- `tts_erp_v2/static/js/runtime-configs.js`
- `tts_erp_v2/static/js/sync-jobs.js`

（`manual-costs` 没有同名旧脚本。）

### lane C —— 旧模板退役（数据工具与内容资产组）

**独占写域：**
- `tts_erp_v2/templates/pages/intercept-configs.html`
- `tts_erp_v2/templates/pages/intercept-requests.html`
- `tts_erp_v2/templates/pages/intercept-stats.html`
- `tts_erp_v2/templates/pages/spu-profit-deterioration.html`
- `tts_erp_v2/templates/pages/video-publish.html`
- `tts_erp_v2/static/js/intercept-configs.js`
- `tts_erp_v2/static/js/intercept-requests.js`
- `tts_erp_v2/static/js/intercept-stats.js`
- `tts_erp_v2/static/js/spu-profit-deterioration.js`
- `tts_erp_v2/static/js/video-publish.js`
- `tts_erp_v2/static/js/ad-daily.js`

**任务：** 同 lane B，逐个确认无消费者后删除模板 + 旧脚本。

**注意**：`ad-daily.js` 的旧页面 HTML 内联在 `tts_erp_v2/api/v2/ad_daily.py`（不是 templates 目录）。该文件属于 lane A 的 `pages.py` 之外的独立文件，**由 lane C 单独认领**：`tts_erp_v2/api/v2/ad_daily.py`。

---

## 四、退役前的强制检查

删除任何旧资源前，必须逐项确认：

```bash
# 1. 该模板/脚本还有没有引用
git grep -n "shops.html" -- tts_erp_v2/ tests/
git grep -n "shops.js" -- tts_erp_v2/ tests/

# 2. 删掉之后有没有测试会挂（测试可能直接读模板内容）
git grep -n "templates/pages" -- tests/

# 3. 页面确实在 MIGRATED_PAGE_IDS 里
```

三项都干净才删。**有引用就先改引用方，不要先删文件。**

---

## 五、验收标准

每个 lane 独立满足：

1. 自己写域内的旧模板和旧脚本全部删除
2. `git grep` 确认无残留引用
3. `bash scripts/test_isolated.sh fast` 通过（不引入新失败）
4. `bash scripts/test_baseline.sh --scope auto --base "$target_sha"` 通过
5. 提交信息：`chore: 退役 <范围> 旧前端实现` + 中文说明

---

## 六、合并顺序

lane B、C 相互独立，可先合。

lane A 依赖 lane B 删除 `manual-costs.html`（因为 `console.js` 的最后一个页面级引用在那里）。合并顺序：

```
lane B  ──┐
          ├──> lane A ──> 最终验证 ──> master
lane C  ──┘
```

---

## 七、待用户确认

- 三个 lane 是否都直接推 master，还是都要先走 PR
- `console.js` 和 `vendor/` 是否要一起清（涉及 app.py 的 public static mount，改动会影响未登录页面的静态资源可达性）
