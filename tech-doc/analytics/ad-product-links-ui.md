# 广告 × 商品投放台账页 — 技术方案

> 状态: **待 plannotator review**（评审通过后实施）
> 关联: `analytics.ad_product_links` 视图（migration 0006，已上线）
> 复用的既有模式: operator console（`/v2/pages/manual-costs` + `js/console.js`，
> 见 `tech-doc/procurement-ui-redesign.md`）、browser session login
> （`tech-doc/browser-login-design.md`）、external API 契约（`tech-doc/external-api.md`）。

> **⚠ 状态（2026-09-11）：本文所述 v3 区间聚合协议已废弃，遗留对象已删除。**
> `analytics.ad_raw` / `analytics.ad_sync_audit` 两张表与 `analytics.ad_product_links`
> 视图已由 **migration 0020** 删除。背景：`ad_raw` 自 v4 逐日协议上线后即冻结
> （现行 `repository.py` 只写 `ad_raw_log`），而 `ad_product_links` 是 `ad_raw` 的
> 唯一依赖者却**零生产消费者** —— SPU ROI 的 `_SQL_ROI_AD`
> （`tts_erp_v2/analytics/spu_roi.py`）一直直接读 `ad_daily ∪ ad_today` 并自行 JOIN
> `commerce`，从不经过该视图。**本文保留为当时的设计记录，不再反映现状**；
> 现行架构见 `tech-doc/analytics/daily-sync-with-coverage.md`，引用面审计见
> `alembic/versions/0020_drop_v3_analytics_leftovers.py` 的模块 docstring。

## 1. 目标与需求拆解

给运营一个只读的「广告投放台账」页面，展示 **广告(计划) × 商品(SPU)** 的投放表现：

1. **列表**：广告与商品的关联明细（每行 = 一个 广告ID×商品ID 对 + 出单量/广告消耗/GMV/窗口）。
2. **搜索**：一个搜索框，输入 **spuid 或 广告ID** 都能过滤（两个 id 列子串匹配）。
3. **翻页**：服务端分页（`limit`/`offset`），与 v2 既有分页约定一致。
4. **聚合**：同一个页面可切到「按广告」或「按商品」维度看汇总行（服务端 GROUP BY），
   顶部合计带跟随当前筛选/维度实时汇总。

数据来源 = `analytics.ad_product_links` 视图（字段口径/语义单点在
`biz-doc/analytics/ad-product-links-view.md`，本方案不重复定义，只引用）：
出单量 = `order_sku_total`（件，TikTok Orders(SKU)，含自然归因）、
广告消耗 = `real_cost_total`、GMV = `order_value_total`；窗口由行内
`observed_days/first_day/last_day` 标注（视图实时聚合 ad_raw 全量，随每日 dump 增长）。

## 2. 总体设计

- **纯后端查询 + 单页 HTML**，零新表 / 零新依赖：数据查询直接走已上线的 DB 视图。
- **新只读 JSON 端点** `GET /v2/analytics/ad-products`（detail|ad|spu 三档，
  服务端分页/搜索/排序/合计）；鉴权角色 **readonly**。
- **新页面** `GET /v2/pages/ad-products` 返回 HTML shell + 新 `static/js/ad-products.js`
  （原生 DOM + fetch，无框架，延续 console.js 家族模式）；未登录浏览器 GET 由
  auth middleware 自动 **302 → login**（`Accept: text/html` 分支，无需页面自处理）。
- **外网**：页面/API 落在既有 `/tts/` 反代下（nginx `proxy_pass` → `:9877`），
  URL = `http://daqiang.nat100.top/tts/v2/pages/ad-products`，**无需新增 nginx
  location**；nginx 只新增 plannotator 评审端口代理（见 §8）。

### 2.1 为什么是新页面而不是塞进 manual-costs

manual-costs 是"采购成本录入"工作台（有状态 tab、写操作、SPU 图上传）；本页是
"投放分析"只读台账，交互面完全不同。按既有惯例每个 operator 页独立 HTML + 独立 JS
（同一 token 家族），避免 console.js 膨胀成页面无关的巨型单体。

## 3. API 契约（新增只读端点）

### 3.1 `GET /v2/analytics/ad-products`

```http
GET /v2/analytics/ad-products?group=detail&q=<id 子串>&sort=cost&order=desc&limit=100&offset=0
```

| 参数 | 类型 | 默认 | 语义 |
| --- | --- | --- | --- |
| `group` | enum | `detail` | `detail`=广告×商品对明细；`ad`=按广告聚合；`spu`=按商品聚合 |
| `q` | str | 空 | 子串搜索；命中 `campaign_id`（广告ID）**或** `product_id`（SPU ID）任一列 |
| `sort` | enum | `cost` | `cost`/`orders`/`gmv`（按当前 group 的汇总列排序） |
| `order` | enum | `desc` | `asc`/`desc` |
| `limit` | int | 100 | 1..500（沿用 v2 分页约定） |
| `offset` | int | 0 | ≥0 |

**响应 envelope**（沿用 reporting `missing-cost-products` 的
`{items, total, ...}` 形态，页首合计带直接消费 `totals`）：

```json
{
  "items": [
    {
      "campaign_id": "1872879663019330",       // 广告ID（TikTok campaign/广告计划）
      "product_id": "1736929955366339831",      // 商品ID（SPU）
      "product_name": "Áo thun nam …",          // detail/spu 档；ad 档为 NULL
      "ad_count": null,                          // spu 档：挂该商品的广告数；ad 档：商品数 → product_count
      "product_count": null,                     // ad 档：该广告挂的商品数
      "order_sku_total": 26,                     // 出单量（件）
      "real_cost_total": "157.4000",            // 广告消耗 —— money 一律 JSON 字符串
      "order_value_total": "472.9100",          // 出单 GMV
      "observed_days": 9,
      "first_day": "2026-08-28",
      "last_day": "2026-09-05",
      "spu_pk": 1448                            // ERP 内部商品 key（目录外 NULL）;2026-09-05 前 = channel_product_id
    }
  ],
  "total": 337,                                 // 匹配总数（分页前，用于分页器）
  "totals": {                                   // 当前筛选集合计（跨分页，顶部队列条）
    "row_count": 337,
    "order_sku_total": 139,
    "real_cost_total": "1207.1700",
    "order_value_total": "3272.0000"
  },
  "meta": { "group": "detail", "q": "", "sort": "cost", "order": "desc" }
}
```

- group 列差异：`detail` 行 = 视图原行；`ad` 行 key=`campaign_id`
  （`product_count`=挂载商品数、商品名列 NULL）；`spu` 行 key=`product_id`
  （`ad_count`=投放广告数、`product_name` 取该 SPU 任一行非空名）。
  三种 group 的数值列口径一致（对 detail 行做 GROUP BY SUM）。
- 聚合实现直接对视图 SQL `GROUP BY`（明细行数量级小，`ad_raw` 只读无写放大）；
  计数字段 bigint，money 字段 `::text`（保留 4 位小数、字符串序列化，遵循
  external-api.md「money is a string」约定）。
- **role 变更**：`tts_erp_v2/middleware/auth.py` `_READONLY_EXACT` 增加
  `"/v2/analytics/ad-products"`（GET，无尾斜杠子路径）；`/v2/analytics/sync*`
  保持 readwrite（更具体前缀判断在前，不受影响）。未匹配路径仍默认 admin
  （fail-closed）不变。
- 无 key → 401；readonly 以下 → 403（与既有错误格式一致 `{"detail": ...}`）。
- 路由放 `tts_erp_v2/api/v2/analytics.py`（同模块加第二个
  `APIRouter(prefix="/v2/analytics")`，`app.py` include 一次；`/endpoints` 自动可见）。

### 3.2 契约文档更新

`tech-doc/external-api.md`：TL;DR 表加一行（readonly）；正文加一节（参数/示例/
Stability=stable 只读，与 commerce 只读端点同级）。`CHANGELOG.md` 记录。

## 4. 前端设计（frontend-design 过程）

### 4.1 主题定位

延续 operator console 已确立的**工业工作台家族**（manual-costs 的
warm paper + warm near-black + burnt sienna、hairline 分隔、零圆角、等宽数字），
新页面的主题是 **「投放台账」—— 广告投放的账页**：
把"广告在给商品花多少钱、换来多少单"读作一本流水账。与 marketing 风格的
dashboard（大渐变卡片）刻意拉开距离；全页只有表格 + 合计带 + 筛选，像账本不像看板。

- 继承 token：`--paper #F4EFE4 / --ink #1B1814 / --accent #B8390E / --rule`…
- 语义色（账页里最常用的两种墨水）：消耗 = 赭红（`--accent` 系），
  出单/GMV = 墨绿 `--ok #2F6B3E` 系；数字全用 `--mono` 等宽，右对齐。
- 字体：系统栈（sans 中文/UI + mono 数字），不引 webfont（内网页零外链）。
- **Signature（让这页被记住的单一元素）**：顶部**「结余带」**—— 一条贯穿的
  mono 合计行（`当前范围 · 共 N 对 · 消耗 ¥x · 出单 y 件 · GMV ¥z · ROI n`），
  随搜索/维度实时跳动；它是"账页"的落点，其余部分保持安静、精确。

### 4.2 布局（ascii）

```text
┌────────────────────────────────────────────────────────────────┐
│ [tts-erp]  广告×商品投放台账            ─ 读取自 ad_raw 观测窗口 │  ← 页头(家族)
├────────────────────────────────────────────────────────────────┤
│ 结余带:  共 337 对 · 广告 228 · 商品 111 · 消耗 1207.17 · …     │  ← signature
├────────────────────────────────────────────────────────────────┤
│ [🔍 搜索 spuid 或 广告ID……]  [明细|按广告|按商品] [共 N 页]      │  ← 工具条
├────────────────────────────────────────────────────────────────┤
│ 广告ID    │ 商品ID   │ 商品名(截断)   │ 出单│ 消耗     │ GMV     │  ← sticky thead
│ 1872…    │ 17369…  │ Áo thun nam…   │ 26  │ 157.4000 │ 472.91  │     斑马纹
│ …                                                             │
├────────────────────────────────────────────────────────────────┤
│ ← 上一页  页码 p / M   下一页 →   每页 100 条                    │  ← 分页脚
└────────────────────────────────────────────────────────────────┘
```

- 模式切换 = 三枚段控件（明细 / 按广告 / 按商品）：**不是 tab 皮肤**——
  切换即改变分组语义（行 key 与列含义同步变化），工具条右侧实时显示当前
  分组的行数/合计。
- 搜索框占位符直接示例两个 id（引导粘贴 spuid 或广告ID），提交去抖 300ms。
- 移动端：表格外层 `overflow-x:auto`，`min-width` 保底列。

### 4.3 交互与文案要点（中文界面）

- 列头可点排序（消耗默认降序；升/降/默认三态图标）。
- 加载态 skeleton / 局部刷新行；空态 = 方向性文案（"这个 ID 下没有投放记录，
  试试完整 spuid 或广告ID"），不摆装饰图；错误态给出可操作的提示 + 重试。
- a11y：可见 focus、`scope` 表头、`aria-live` 合计带、`prefers-reduced-motion`
  关闭过渡、键盘可达。
- 401（cookie 过期）→ fetch 侧跳 login（沿用 console.js 做法）。

## 5. 文件变更清单

| 文件 | 变更 |
| --- | --- |
| `tts_erp_v2/api/v2/analytics.py` | 新增 `APIRouter(prefix="/v2/analytics")` + `GET /ad-products`（3 组 SQL：detail/ad/spu + 搜索 + 排序 + COUNT + totals） |
| `tts_erp_v2/middleware/auth.py` | `_READONLY_EXACT` 加 `"/v2/analytics/ad-products"` + 注释 |
| `tts_erp_v2/app.py` | include 新 router（同一 `analytics` 模块二次 include 或拆子模块，实施时定） |
| `tts_erp_v2/api/v2/pages.py` | 新增 `GET /v2/pages/ad-products` HTML shell（链 `../../static/vendor/bootstrap.min.css` + `/static/js/ad-products.js`，相对路径） |
| `tts_erp_v2/static/js/ad-products.js` | 新 JS：prefix 推导、fetch+envelope 解包、搜索/排序/分页/三模式状态、结余带渲染 |
| `tests/api/test_analytics_ad_products_api.py` | TDD：detail 分页/搜索(广告ID、SPU 各一例)/排序；ad、spu 聚合值对账（对照视图直接 SQL）；totals 跨页合计；401/403/limit 校验。造数 = 直接 INSERT ad_raw TEST_ 5 元组行（复用 tests/analytics/view 测试的 helper 思路；autouse 清理 ad_raw TEST_ 行） |
| `tests/api/…auth 或 middleware 契约` | 若角色矩阵有集中断言文件则补 `ad-products → readonly`；无则靠 401/403 用例覆盖 |
| `tech-doc/external-api.md` | TL;DR 行 + 章节 + Stability |
| `tech-doc/analytics/ad-product-links-ui.md` | 本方案（实施后勾状态） |
| `CHANGELOG.md` | 条目 |
| `biz-doc/analytics/ad-product-links-view.md` | §4 常用查询补「页面即此视图的 operator 前台」一句（可选） |

## 6. 实施顺序（评审通过后）

1. 测试先行：`tests/api/test_analytics_ad_products_api.py` 红灯。
2. 实现端点（视图 SQL → 三 group → 搜索/排序/分页/totals）→ 绿灯。
3. auth 精确项 + app include；`pytest tests/api -q` 回归（role/access_log 用例）。
4. 页面 HTML + JS；浏览器自检（本机 :9877 直连 + `/tts` 前缀两种路径）。
5. 文档（external-api/CHANGELOG/biz-doc）。
6. 门禁 `bash scripts/test.sh fast` 0 fail（含他人 lane 文件不碰）。
7. `restart.sh` 重启 API → curl 冒烟（healthz / 新端点 401→带 key 200）。

## 7. 部署与外网访问（NGINX 为 docker 部署：`nginx-gw`）

- 现状（已确认）：`nginx-gw` 容器 host 网络，listen `:9899`；公网
  `daqiang.nat100.top`（natapp 隧道）→ `:9899`；`location /tts/`
  `proxy_pass http://127.0.0.1:9877/`（含 `X-Forwarded-Prefix /tts/`）。
- **UI/API 外网访问零配置**：新页面与端点都在 `/tts/` 下自动可访问：
  `http://daqiang.nat100.top/tts/v2/pages/ad-products`
  （浏览器未登录自动 302 到 `/tts/v2/auth/login?next=…`，登录页回跳）。
- nginx 配置文件：`/home/schan/setup/nginx/conf.d/services.conf`；
  改后 `docker exec nginx-gw nginx -s reload`；冒烟 `curl -sI` 各路径。

## 8. plannotator 评审端口（nginx 新增 location）

- plannotator 会话起本地 HTTP 服务：固定 `PLANNOTATOR_PORT=19432`
  （远程/SSH 模式默认 19432，仍显式设置防漂移）。
- `services.conf` 新增：

```nginx
# plannotator 评审 UI（本地 :19432，经公网评审用）
location /plannotator/ {
    proxy_pass              http://127.0.0.1:19432/;
    proxy_http_version      1.1;
    proxy_set_header        Host              $host;
    proxy_set_header        X-Real-IP         $remote_addr;
    proxy_set_header        X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header        X-Forwarded-Proto $scheme;
    proxy_read_timeout      3600s;             # 评审可能长时间挂着
}
```

- 若前端资源用绝对根路径（`/static/...` 等）导致子路径渲染坏，实测后加
  `sub_filter` 或改 proxy 策略（评审启动后 curl 页面 HTML 验证，见 §9 冒烟）。
- 评审命令（评审本方案 doc；阻塞到 reviewer 提交/关闭，输出回 stdout）：

```bash
PLANNOTATOR_PORT=19432 plannotator annotate tech-doc/analytics/ad-product-links-ui.md --json
```

- 外网评审 URL：`http://daqiang.nat100.top/plannotator/`。

## 9. 冒烟清单

- [ ] `curl http://daqiang.nat100.top/tts/healthz` → 200 ok
- [ ] 无 key `GET /tts/v2/analytics/ad-products` → 401
- [ ] readonly key `GET /tts/v2/analytics/ad-products?group=spu&q=…&limit=5` → 200 且 totals 正确
- [ ] 浏览器 `/tts/v2/pages/ad-products` 未登录 → 302 login → 登录回跳页面
- [ ] `/plannotator/` → 200（plannotator 运行期间）
- [ ] 页面三模式切换/搜索/翻页人工过一遍（operator 侧）

## 10. 风险与开放问题

- 视图窗口是"全量累计"（ad_raw 不 purge）：页面需靠 `observed_days/last_day`
  与顶部文案提示口径（如无特别声明，后续可加日范围参数——本期不做，避免范围蔓延）。
- 多 SPU 广告（总收入型 campaign）行数会随商品挂载数增长；分页上限 500 已够，
  聚合模式不受影响。
- 结余带"ROI"仅展示（GMV/消耗，分母 0 显示 `—`），不下发，避免口径争论落在 API。
- 他人 lane 未提交文件（`sync_worker/watermarks.py` 等）按仓库约定不碰；实施
  全程 `git` 只动本方案 §5 清单内文件。
