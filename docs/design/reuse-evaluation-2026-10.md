# 开源组件复用评估与落地记录（2026-10）

> 来源：按 `AGENTS.md` §5.1（Reuse-first implementation policy）对前后端做的
> 一轮系统性复用审查。本文件记录每项候选的**结论与证据**，避免后续重复评估。

## 已落地

### 1. tenacity 替换手写重试退避（merged）

- 位置：`tts_erp_v2/proxy/tts_shop/client.py`、
  `tts_erp_v2/jobs/miaoshou/purchase_price_clean.py`；`pyproject.toml` 新增
  `tenacity>=9.1,<10`（Apache-2.0, <https://github.com/jd/tenacity>）。
- `client.py`：重试循环改由 `tenacity.Retrying`（`wait_random_exponential` +
  `retry_if_exception_type(_RetryableAttempt)` + `reraise=True`）驱动；
  只有可重试条件（网络错误 / 5xx）进入 `_RetryableAttempt`，401/403/429/业务
  4xx 仍然直通。最终 `TransientProxyError` 消息文案不变。
- `purchase_price_clean.py`：分页 fetch 用 `wait_incrementing(start=1,
  increment=1)`，与原 `time.sleep(attempt + 1)` 等价。
- **明确不动**：`tts_erp_v2/proxy/miaoshou/retry.py` 的
  `paginate_with_retry`（空页即限流分类器是 237→20 条静默截断事故的修复，
  见 `common-bugs.md`）；`proxy/miaoshou/rate_limit.py`；
  `sync_worker/scheduler.py` 的 tick 间隔 sleep。
- 附带：`sync_worker/main.py` 手写 argv 解析改为标准库 argparse（不引 click）。
  CLI 返回码契约不变（help→0、usage error→2、未知子命令 stderr 含原词）。

### 2. 前端 Intl.NumberFormat / Intl.DateTimeFormat 缓存实例（merged）

- `spu-profitability-page.js`：`fmtMoney/fmtRatio/fmtPct/fmtQty` 改为模块级
  缓存的 `Intl.NumberFormat`（原来每次 `toLocaleString` 都重建 formatter，
  该页一次渲染数百单元格）。
- `intercept-stats.js` `formatNumber`、`intercept-requests.js` 时间格式化同理。
- 纯浏览器内置 API，零新依赖，输出格式与 locale 完全不变。

### 3. uPlot 替换 intercept-stats 手绘 div 柱状图（merged）

- `static/vendor/uplot.iife.min.js` + `uplot.min.css`（1.6.32，MIT，
  <https://github.com/leeoniya/uPlot>，jsDelivr 自托管，登记于
  `static/vendor/NOTICE.md`）。
- `intercept-stats.js` 的「最近 7 天每日趋势」从 div 进度条改为
  `uPlot.paths.bars()` 柱状图：坐标轴、hover 值、resize 自适应。
- 按域名/方法/状态码分布保留 div 横条（它们是带内联标签/百分比的列表，
  不是图表语义）。

### 4. pages.py 迁移 Jinja2 模板（merged）

- `tts_erp_v2/api/v2/pages.py` 3129 → ~650 行；10 个页面 HTML 常量提取到
  `tts_erp_v2/templates/pages/*.html`。
- 渲染：模块级 `jinja2.Environment`（`FileSystemLoader` +
  `select_autoescape(["html"])` + `keep_trailing_newline=True`），新增
  `jinja2>=3.1` 依赖（BSD-3-Clause）。注意：Starlette 1.7 的
  `Jinja2Templates` 只收 `env=`，不再转发 `**env_options`。
- 模板全局函数：`js_version` / `css_version`（SHA-256 内容戳，替代
  `__JSV_*__` 占位符）、`sidebar(current_page)` / `sidebar_css(current_page)`
  （侧边栏注入，Markup 包装，Python 侧 `_sidebar_html`/`_SIDEBAR_CSS`/
  `_SIDEBAR_TOGGLE_JS` 原样保留，测试断言不受影响）。
- 逐页与迁移前输出 diff 验证：除两处无渲染影响的空白外逐字节一致。
- **顺带修复隐性 bug**：manual-costs 排序箭头 CSS `content: "\2191"` /
  `"\2193"` 在原 Python 字符串中被八进制转义吞成 `\x91"1"` 乱码；模板化后
  Jinja 不做 Python 转义，箭头按作者本意渲染。

## 评估后明确不替换（附证据，勿重复尝试）

### A. slowapi 替换 `middleware/rate_limit.py` —— 不适用

现有实现不是普通限流中间件，而是跨模块的**共享计数器契约**：

1. key 来自 `AuthMiddleware` 写入的 `scope['api_key_hash']`（slowapi 按
   route decorator + 自有 storage 工作，无法读 ASGI scope 键）；
2. `shared_hit()` 被 auth 拒绝路径复用做暴力破解节流（`middleware/auth.py`、
   `access/_access.py`）；
3. `reset_shared()` 支持**保留 bucket 的就地限额变更**（admin 端点
   `/v2/admin/reset-rate-limit`）；`limits` 库不支持原地改限额保留计数；
4. 429 契约（`Retry-After` 计算、body 格式、`X-RateLimit-*`）被
   `tests/api/test_middleware.py` / `test_admin.py` 锁定。

替换 slowapi 需重写安全关键路径的粘合层，回归风险大于收益；滑动窗口本体
约 25 行 deque 逻辑，属 AGENTS.md §5.1「trivial logic 本地实现更清晰安全」。

### B. Tabulator 替换 spu-profitability 自研表格 —— 不适用（勿盲目重试）

主结果表只是该页复杂度的小部分，以下**自研契约无法映射到 Tabulator**：

1. **服务端排序**：`<th data-sort>` 是「点击→带 sort 参数重新请求后端」契约，
   `tests/analytics/test_spu_profitability_sorting.py` 直接解析模板 HTML 断言
   `data-sort` ↔ `SortField` 枚举映射。Tabulator 自渲染表头 DOM，
   `<table class="op-table">` 消失，契约与测试全部需要重建。
2. **`data-column-id` 双契约**：列显隐（视图配置 + localStorage 偏好）和
   `spu-roi.css`（1143 行）的断点列裁剪 + 首列 sticky 都 key 在这个属性上；
   Tabulator DOM 没有这些属性。
3. **`data-tip` 自定义 tooltip**（`#ops-tip`）：表头与单元格警示标记
   （缺成本/高退款/≈未结算）都走它；Tabulator 表头/单元格需用 formatter
   逐一重建。
4. **行点击下钻**：tbody 事件委托 `tr[data-spupk]` → 下钻面板
   （orders/settlements/cases/ads 懒加载 tab），与 Tabulator rowClick
   模型需重新对接。
5. 表格本体 `rowMarkup` 仅约 60 行；迁移后 70% 的周边机器（下钻、视图配置、
   汇总带、分页、CSS 裁剪）仍需围绕 Tabulator 的 DOM 模型重写，且该页是
   测试锁定最重的页面（3 个测试文件断言 JS/模板内部结构）。

若未来重提：前提是先做模板/JS 的列元数据单一事实源（column defs 驱动
排序契约与 CSS 生成），再评估表格库；届时需浏览器可视化回归验证。
