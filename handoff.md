# handoff.md — tts-erp 跨 session 交接笔记

> 🔄 **当前在途工作注册（谁在改什么 / 谁接手）：先读 `handoff/ACTIVE.md`**（AGENTS.md §12.1）
>
> 上次 session: 2026-09-13（P0 ad_daily purge recovery + 双 gate 加固部署）
> 上次 session 主题: **prod plugin.ad_daily 被误清 13,683 行 → 恢复 15,437 行（含 chrome backfill 增量）+ purge 端点双 gate 加固 + conftest prod-shape hard fail**

## TL;DR (2026-09-13 P0 ad_daily purge recovery)

**背景**：9-13 08:19 UTC（北京时间 16:19），有人在 prod tts_erp 库跑了
`tests/api/test_admin_purge.py::test_purge_plugin_data_clears_ad_tables`（worktree `.env` 软链到
主仓 prod `.env` + 裸 pytest 走 prod），session 1537 第 2 段事务 = `purge_plugin_data` 端点的 11 个
SELECT COUNT + 2 个裸 DELETE（清空 14,719 行 → 残 1,036 行）。

**恢复（已完成）**：
- 06:00 preserved pgdump 14,306 行 + 9-13 早上 prod 残骸里 staging 漏的 1,131 行（chrome backfill）= **15,437 行恢复**
- 维度：111 products / 246 campaigns / 65 days（7-10 ~ 9-12）/ 总成本 ¥6,389.59
- id_seq 修复到 59,500（next=59,501）
- 原 prod 残骸 1,138 行保留在 `plugin.ad_daily_rescue_20260913`（紧急回滚源）

**加固（已落地，commit fix/recover-ad-daily-purge-guard）**：
1. **`tests/conftest.py`** — prod-shape dbname WARNING → `pytest.exit(2)` hard fail；只有
   `TTS_ERP_TEST_OFF=1` 临时绕过（且打醒目 banner `LIVE DATA AT RISK`）
2. **`tts_erp_v2/api/v2/admin.py::purge_plugin_data`** — 双 gate：
   - Gate 1: `_is_prod_shaped_db()` 检查 `TTS_ERP_DB_URL`，prod-shape 返 403（除非 `ALLOW_PROD_PURGE=1`）
   - Gate 2: 必须 `?confirm=true` 才真删，无 confirm = dry-run（返行数 + `dry_run: true` + `next_step` 提示）
   - role 复位：admin（2026-09-10 bfb6b71 降到 readwrite 的改动回滚）
3. **`tests/api/test_admin_purge.py`** — 重写以适配双 gate，新增 dry-run 测试

**事故完整复盘**：见 `tech-doc/incident-reports/2026-09-13-ad-daily-purge.md`（含 PG log 时间线、
5-Why 根因、HTTP access log 为什么看不到、恢复脚本、教训）。

## TL;DR (2026-09-11 PLUGIN_ARCH_CLEANUP — 插件数据物理隔离)

**背景**：广告 dump 无 server-side 同步路径，但 `api-managed` 守卫（`ae843a1`）把
`shops.data_source='api'` 店铺的插件 dumps **全域静默吞掉** → 广告数据永远进不来。
方向：api 同步数据与插件 dump 数据**按 schema 物理隔离**，不再需要来源判定。

**四个 lane（均为串行 worktree，合并后 prod 已迁移 + 重启 + 冒烟 8/8）**：

| lane | 内容 | migration | prod |
| --- | --- | --- | --- |
| 1 | AGENTS.md §6 加「不得删除/截断 prod 库数据」红线 | — | — |
| 2 | `chrome_sync` schema → `plugin`（含包 `plugin/orders/`） | `0023_chrome_sync_to_plugin` | ✅ |
| 3 | `analytics` 5 表 → `plugin` + `DROP SCHEMA analytics`（含包 `plugin/ads/`、job 改名 `plugin.ad_merge_today2daily`） | `0024_analytics_to_plugin` | ✅ |
| 4 | 删 `commerce.shops.data_source` + 拆两处 api-managed 守卫 + 删 `shop_is_api_managed()` | `0025_drop_shops_data_source` | ✅ |

- `data_source` 拆卸后，插件 dumps **不再被拦截**（已用「无效 kind 探针」在 prod 验证：
  原返回 `200 api_managed` → 现走到校验返 `400`）。
- `backend/commerce.shops` 删列前已备份：`backups/commerce_shops_pre_0025_20260911_160458.sql`。
- 每个 lane：test 库全量 fast **0 新 fail**（13 个 pre-existing 逐条一致）、ruff 集合一致。

**❗ 遗留缺口（待用户决策，未修）**：`plugin.*` 的时间字段约定不完整 ——

- `plugin.raw_log` **无 `updated_at` 列**；
- 7 张订单表（orders / order_lines / shipments / tracking_events / settlements /
  settlement_details / raw_log）**无 `BEFORE UPDATE` 触发器**。

二者是 `chrome_sync` 时期就存在的遗留（该 schema 从未被
`tests/db/test_time_fields_convention.py::V2_SCHEMAS` 覆盖）。lane 3 修 `V2_SCHEMAS` 时
发现了它们，但为避免引入新 fail，**只移除已消失的 `analytics`、未加入 `plugin`**。
需要时另开 lane 补列 + 加触发器，并把 `plugin` 加入 `V2_SCHEMAS`。

### ❗ 订单域插件同步：用户 2026-09-11 拍板「先不处理，先观察」（选项 B）—— 属预期状态，勿当 bug 修

- **背景**：订单/物流/结算已有 server-side API 同步（`tiktok.orders` / `order_detail` /
  `logistics` / `finance` / `after_sales` jobs → `commerce.*` / `fulfillment.*` /
  `finance.*`），插件侧仍有一套并行的 order-sync；插件的 TikTok 请求**不经过 ad 的
  `tiktokRequestPacer`**（自带 3s/单 的 N+1 间隔），因此两条链路会争抢上游配额。
- **lane 4 拆守卫后的新变化**：order dumps 从「被静默丢弃（`200 api_managed`）」变为
  **真正落库** → `plugin.orders / order_lines / shipments / tracking_events /
  settlements / settlement_details / raw_log` **会开始增长**（之前一直 0 行）。
- **预期现象**：同一事实在两处各存一份 —— `commerce.sales_orders`（API）vs
  `plugin.orders`（插件）、`fulfillment.shipments` vs `plugin.shipments` 等。
  **这是用户拍板的「schema 物理隔离 + 后续再选读哪个」设计，不是双写 bug。**
- **已知代价**（接受）：① 上游请求配额轻微争抢；② 7 张插件订单表无业务读者却持续增长；
  ③ 上文的时间字段缺口。
- **若日后要下线**：应作为**整个插件订单域下线**（7 表 + `tts_erp_v2/plugin/orders/` +
  `api/v2/order_sync.py` router + `tests/plugin/orders/` + **chrome-plugins 侧 order-sync 全套**），
  **不要单独删 `plugin.raw_log`**（它是 6 张订单表的 FK 父表）。下线前需先确认插件订单同步
  是否仍是「API scope 缺失时的兜底」。

**完整记录**：`handoff/PLUGIN_ARCH_CLEANUP.md`（决策快照 / 每 lane 改动面 / 实测经验）。

---

## TL;DR (2026-09-11 v4 dump campaign-level rows 双端对齐)

**修复 Bridge nook 店铺 09-10 18:45 UTC 起 dumps 500 KeyError 持续失败**（`75c84c5` tts-erp
merge + `a70d078` handoff；`8595167` chrome-plugins merge + `e35fc2c` handoff）：

1. **服务端** — `tts_erp_v2/analytics/repository.py` 加 `_PRODUCT_LEVEL_ENDPOINTS` 白名单
   (post_product_list + post_session_list)，不在白名单的 endpoint（如
   campaign_opt_log_list）走 `_archive_raw_log_only` 路径：rows 只入 ad_raw_log
   (response_body 完整保留)，不入 ad_daily/ad_today/ad_monthly，product_id 存 NULL。
   `tts_erp_v2/api/v2/analytics.py` 在 dumps 响应里加 `status='campaign_level'` 字段。
2. **插件端** — chrome-plugins `entrypoints/background.ts` 加 `extractRowsForV4Dump()`
   helper：campaign-level endpoint → dump.rows=[]，product-level endpoint 走原
   `extractRowsFromResponse`。三处调用 (daily/today/monthly) 全部换过去。
3. **协议不变量** — 双端对齐让 "dump.rows 必须是 product-level 行" 成为 v4 协议明确
   不变量。服务端 `is_product_level_endpoint()` 是公开 API（`__all__` 暴露），未来新
   endpoint 默认走 product-level 保守暴露 KeyError，让开发者补白名单（AGENTS.md §6
   fail-loud）。
4. **测试** — tts-erp 加 7 个新测试（3 个 dumps endpoint + 4 个 repository 层），全部通过；
   chrome-plugins 加 9 个新测试，全量 668 测试通过。master HEAD 仍是 19 个 pre-existing
   fail（跟我无关），0 新 fail。
5. **线上验证** — 重启后手工 curl 测 campaign_opt_log_list dump → 200 + `status=campaign_level`
   - ad_raw_log 写 1 行 product_id=NULL。stderr KeyError 计数停在上轮 809 不再涨。
6. **postswhitch-smoke 8/8 通过** + master push 成功 + 双方 worktree 收尾清理。
7. **已知遗留** — Bridge nook 当前被 `feature/api-managed-guard` lane 标 api_managed，
   ad_*表数据来源实际是另一条 path；本次修复重点是让 plugin 上传不再 500，
   实际 ad_* 数据恢复需要看 api-managed 守卫 review。

**修过的根因**：lane `feat(analytics): v4 结构化 rows 同步协议`（`124c689`，09-10 merge）
假设每行都有 product_id，但 campaign_opt_log_list 是 campaign-level 变更事件永远没
product_id → KeyError → dumps 500 → plugin 持续重试失败 → 14512 条 plugin_logs 错误。

## TL;DR (2026-09-07 AGENTS.md 多 agent 规则补漏 + §11 细化)

**AGENTS.md 多 agent 协作规则审查并补漏**（`95b399b` merge + `a203fd1` merge，2 条 lane）：

1. **§6 合法清理手段清单**：禁止 `git reset --hard / checkout -- . / clean -f`，新增合法替代：`git revert` / `git stash / restore / checkout -- <file>`（指定文件非全清）
2. **§11 worktree 收尾**：merge 后 master 重跑 `scripts/test.sh fast`（新节点）+ `git log --oneline master..<branch>` 预检（防 -D 丢 commit）
3. **§11 .env 软链警告**：所有 worktree 共享 `.env`，任一 lane 临时改 `.env` 污染全部 lane；调试后必须还原或用 `.env.local` 覆盖
4. **§12.1 单写者规则**：ACTIVE.md 同一时刻只允许一个 session/agent 写入；写前 git diff 确认、写后立即 git add
5. **§12.3 接手步骤 6**：接手后 ACTIVE.md 必须更新（原 owner 改 abandoned + 新增接手行）
6. **§12.4 错峰量化**：flock / 轮询 / 分 ephemeral DB 三种串行方案
7. **§11 §11 测试规则细化**："merge 后必须 0 fail" 硬规则在 master HEAD pre-existing fail 下不可达 → 改为按 lane 代码改动面分类判定（`git diff <merge-base>..HEAD -- 'tts_erp_v2/**' 'tests/**' | wc -l` = 0 → 文档-only lane 直接 push；> 0 → fail-before/fail-after diff 对比）

**stash@{0} 处理**：stash 内容（"master-wip-before-spu-image-mirror-merge"）apply 触发 7 个 conflict，评估后放弃（master 后续 commit 已吸收核心内容），snapshot 存 `/tmp/stash-0-snapshot-*.patch`。

**新工具**：`scripts/test_lock.sh`（§12.4 flock 包装，防并发测试互清）。

**已知问题**：settlement-zero-components merge 引入 61 新 fail（tests/api/ 下 17 文件），为代码 lane 应由该 owner 按 §11 新规 fail-before/fail-after 对比处理。

**活跃 worktree**（截至 2026-09-07 21:45）：channel-account-by-external / docs-spu-roi-v7 / fx-test-isolation / spu-roi-v7 / spu-roi-v7-frontend（共 5 条）。

## TL;DR (2026-09-06 结余带 10 格重构 + 订单行→SPU 关联断裂修复)

- **结余带 10 格重构**(merge 5cbf518):去掉 SPU 数,新增 GMV(全部订单销售额 M6+M6b)/有效单量/
  总单量/取消单量;全损货损改名全损退款(数值=return_loss 不变);每格带 ? 口径气泡;栅格
  xs2/sm3/md4/lg5。后端 totals 新增 4 键(_SQL_ROI_ORDER_SCOPE 跨可见 SPU 全局去重)。
- **订单行→SPU 关联断裂修复**(merge 833d823):8-31 后 orders/order_detail 写行 spu_pk 恒 NULL
  ("later join" 注释但无 job 执行)→ SPU 级报表整单丢失。修复=写时目录解析(orders/order_detail)+
  products 同步后 backfill(spu_link.py)+ oneoff 存量(scripts/oneoff_backfill_order_line_spu.py,
  已 apply 241 行)。修复后 totals: order 431→**510**、cancelled 10→**26**、total 441→**536**、
  GMV 10118→**12126**。sync-worker 已重启。测试 +8 全绿(jobs_tiktok 全域,mirror 已知环境失败除外)。
- 已知环境失败(非本 lane,另一 session 在修):`tests/fx/*` + `tests/api/test_fx_api.py`(fx.sync 真实
  snapshot 干扰,见 ACTIVE fix/fx-test-isolation)、`tests/jobs_tiktok/test_spu_image_mirror_job.py`
  (live spu.image_mirror job 在 dev DB 残留 MIRROR_DOWNLOAD_FAILED 行)。全量 0 fail 待 fx lane 落地。
- 提醒:master WT 有其它 lane 未提交 WIP(console.js 等)——收尾前先看 ACTIVE。

## TL;DR (2026-09-06 spu-roi Bootstrap 重构 + 手机端适配)

- spu-roi 页(`/v2/pages/spu-roi`)重构为 **Bootstrap 5.3.8 栅格/工具类布局**:结余带 row-cols
  (xs2→md4→lg7)、工具栏 flex-wrap 纵向堆叠、`<details>` 列开关、`.table-responsive` 横滚 +
  首列/表头 sticky(≤lg)、小屏 nth-child 裁次要列(广告数/平台GMV/ROI₀/件数),580→
  页面样式仍 warm-paper 家族(`--bs-*` 变量收编 + 零圆角)。spu-roi.js **零改动**(纯 CSS/HTML)。
  merge 6136245,已重启 + 公网冒烟,已 push。
- 已知环境失败(非本 lane,另一 session 在修):`tests/fx/*` + `tests/api/test_fx_api.py`(fx.sync 真实
  snapshot 干扰,见 ACTIVE fix/fx-test-isolation)、`tests/jobs_tiktok/test_spu_image_mirror_job.py`
  (live spu.image_mirror job 在 dev DB 残留 MIRROR_DOWNLOAD_FAILED 行)。全量 0 fail 待 fx lane 落地。
- 提醒:master WT 有 feat/cursor-hasdata-cache lane 的未提交 WIP(analytics.py/repository.py/
  conftest.py/console.js 等,已在 ACTIVE 注册)——任何人收尾前先看 ACTIVE。

## TL;DR (2026-09-05 oauth_receiver DROP)

**v1 `oauth_receiver` 库已按官方流程整体废弃并 DROP**（AGENTS.md 原计划保留至 ~09-26，
本次 2026-09-05 提前收口）：

1. 归档：`/home/schan/backups/oauth_receiver_v1_legacy_20260905T134439Z.sql.gz`（3.1KB，
   含 `DROP TABLE IF EXISTS public.oauth_tokens` + `CREATE TABLE`，可完整恢复）。
2. 凭证单源已完全收口到 `integration.credentials`（tiktok/7494763368967603447 Bridge nook +
   miaoshou/ak_... 均已就位且 scope 齐全）——v1 oauth_receiver 库失去回滚价值。
3. 拆 systemd unit + `.env` 的 `OAUTH_DB_URL` / `OAUTH_DB_ENCRYPTION_KEY` 两行
   （`TTS_ERP_FERNET_KEY` 是同一 Fernet key，保留不动）+ 删 `schema_oauth.sql` +
   `scripts/regen_schema.py` 单库化。
4. **CHANGELOG / AGENTS.md / setup/tts-erp.md / tech-doc/api-key-auth-design.md /
   tech-doc/analytics/reorg-plan.md 已同步**。`tech-doc/_archive/` 与 ADR 历史保留。
5. **回滚路径**：恢复 oauth-receiver 库 + 跑 `tech-doc/_archive/migrate-v1-to-v2-2026-08-29/
   scripts/re_encrypt_credentials.py` 把 legacy 格式转回 v2 envelope + 恢复 .env / unit。
6. 验证：`bash scripts/test.sh fast` exit=0 全绿；`:9877/healthz` 返 `ok/enforce`；sync-worker
   进程无崩溃（已跑 5112 jobs 历史）。
7. ⚠️ 测试稳定性预存问题（CHANGELOG 9-5 fix 条目已记录，间歇性偶发，master HEAD 预存，
   与本次清理无关）——不需要重做。

## TL;DR (2026-09-05 public.* DROP)

**v1 `public.*` 遗留层已按官方流程归档删除**（观察期提前收口，原定 ~09-26）：

1. 归档：`/home/schan/backups/tts_erp_public_v1_legacy_20260905T110814Z.sql.gz`（19 表 schema+data，可完整恢复）。
2. DROP 19 张 v1 业务表 + 3 个孤儿函数；`schema_tts_erp.sql` 重生成（-839 行）。
3. **不要动 `public` schema 和 `public.fn_touch_updated_at()`** —— 41 个 v2 updated_at 触发器依赖它
   （migration 0001；`tests/db/test_time_fields_convention.py` 锁定）。
4. ~~oauth_receiver（独立库 :5432/oauth_receiver）未动，仍按原观察期 ~09-26 保留。~~
   **2026-09-05 同日已 DROP**，见上一段 "TL;DR (2026-09-05 oauth_receiver DROP)"。

## TL;DR (2026-08-31)

procurement UI 重做 + MinIO SPU 图片存储全部落地并提交到 `feature/procurement-ui`：

1. **Backend**：`tts_erp_v2/storage/minio_client.py` + `/v2/spu-images/*`（presigned upload/confirm/list/delete）+ `procurement.spu_images` 表（`schema_storage.sql`，**生产库还没 apply**）。
2. **Frontend**：`/v2/pages/manual-costs` 壳页面 + `/static/console.{css,js}`（shop switcher + 三 tab 工作台）。
3. **修了两个集成 bug**：`/endpoints` 在 FastAPI ≥0.141 lazy router 下丢路由（`_iter_resolved_routes`）；`GET /v2/spu-images` 无 filter 时 `AmbiguousParameter` 500（`CAST(:cp_id AS bigint)`）。

**预存在的基线问题（master 上同样复现，与本分支无关，还没修）**：

- `tests_v2/sync_worker` + `tests_v2/test_models_smoke.py::test_sync_jobs_lifecycle` 失败 —— 真库 `sync_cursors`/`sync_jobs` 有重复行。
- `tests_v2/migration` 跑到 ~59% hang 住（怀疑等 DB 锁）。
- `tests_v2/jobs_tiktok` 5 个失败。

**已收尾（2026-08-31）**：已 merge 回 master（`aca4389`，/endpoints 冲突取 master 的 `_walk_v2_routes`）；`schema_storage.sql` 已 apply（幂等）；MINIO_* 配置已入主 `.env`；生产 :9877 已重启并冒烟通过（/endpoints count=38，spu-images 路由在线）。worktree `~/tts-erp.procurement` 已删。主 worktree 仍有另一 session 的 analytics_sync WIP 未提交（`analytics_sync/app.py`、`middleware/auth.py`、`tests_v2/api/test_auth_login.py` 等）。

## TL;DR (2026-08-25)

三个 drift 全部修完，e2e smoke + 单元测试全部绿：

1. **`/healthz` 不再撒谎** —— `oauth_receiver.token_count` 现在从 `oauth_tokens` 表真实 `SELECT COUNT(*)` 读，不再是 in-memory `_token_history`（永远 0）。
2. **`tts_erp.shops` 被填充** —— FastAPI startup lifespan + `POST /admin/shops/backfill` 都触发 backfill，幂等。`_tiktok_proxy` 在订单详情路径上也调 `persist_shop`（双保险）。
3. **`schema.sql` 重新生成** —— `scripts/regen_schema.py` 从真实 PG 拉出含 **24 张表** 的 schema（1 oauth_receiver + 23 tts_erp），fresh DB clean apply **0 errors**。

新文件：

- `tdd/_backfill.py` — `backfill_shops_from_oauth()` 幂等函数
- `scripts/regen_schema.py` — schema.sql 重新生成工具
- `tdd/test_healthz_token_count_fix.py` — 4 healthz 测试
- `tdd/test_shops_backfill.py` — 3 backfill 测试

## 上次 session (2026-08-16) 主题

## TL;DR

**所有端到端流程已通**：oauth-receiver 续期 cron 在跑、tts-erp 服务在 9877 监听、**`/sync/orders` 已成功入库 365 个订单**（37 页翻页）、`/sync/statements` 入库 31 条对账单、`/sync/payments` 入库 10 条付款记录。

**最终可用的端点**（2026-08-16 实测）：

### Order 模块

- `POST /orders/search` ✅ — 拉取订单列表（body 只放过滤条件，paging/sort 全在 query string）
- `GET /orders/<id>` ✅ — 拉取单个订单（内部转发到 `/order/202309/orders?ids=<id>`，因为 path 版的 `/orders/{id}` 返回 36009009）
- `POST /sync/orders` ✅ — 拉取并入库
- `POST /sync/order/<id>` ✅ — 拉单个并入库
- `GET /db/orders` / `GET /db/orders/<id>` 等 ✅ — 读本地 DB
- `POST /orders/<id>/cancel|confirm|update_status|shipping_info|verify_shipping` ❌ → **返回 501**（TikTok 202309 Order 模块是只读，写操作在 Fulfillment / Reverse Logistics 模块）
- `GET /orders/<id>/tracking|risk|buyer|recipient` ❌ → **返回 501**（同上，202309 Order 模块不暴露这些）
- `/orders/list` ❌ → 36009009 "Invalid path"（路径不存在，所有 list 走 /orders/search）

### Finance / Statement 模块（get-statements-202309）— 2026-08-16 新增

- `GET /finance/statements?shop_id=X&page_size=50&sort_field=statement_time&sort_order=DESC` ✅ — 拉对账单
- `GET /finance/payments?shop_id=X&page_size=50&sort_field=create_time&sort_order=DESC` ✅ — 拉付款记录
- `POST /sync/statements` / `POST /sync/payments` ✅ — 拉取并入库（自动翻页）
- `GET /db/statements?shop_id=X&limit=50` / `GET /db/payments?shop_id=X&status=PAID&limit=50` ✅ — 读本地 DB
- **已知不存在**（36009009）：`/finance/202309/statements/{id}`（无 detail）、`/statements/{id}/transactions`、子 records endpoints、downloads、balance

## 完成了什么

1. ✅ **oauth-receiver cron 续期** — `0 2 * * *` 调 `refresh_tokens.sh`，已加到 schan crontab
2. ✅ **tts-erp 服务** — 端口 9877，REAL 模式，DB `tts_erp` 5 张表都建好
3. ✅ **OAuth 间接获取** — tts-erp **不**直读 PG `oauth_tokens`，全部走 `http://127.0.0.1:9876` HTTP
4. ✅ **HMAC-SHA256 签名** — 实测通过 `106001 invalid sign` 这关
5. ✅ **Order API 代理** — 全部 12 个端点都接了（/orders/list 已移除，action 端点全部返回 501）
6. ✅ **同步逻辑** — `/sync/orders` 翻页拉（365 单/37 页实测成功），`/sync/order/<id>` 单拉
7. ✅ **本地 DB 读** — `/db/orders`（带 status 过滤，name 字段）, `/db/orders/<id>` 等
8. ✅ **4 份文档** — `setup/tts-erp.md`、`AGENTS.md`、`README.md`、`handoff.md`

## 关键路径速查

| 路径                                                | 说明                                |
|-----------------------------------------------------|-------------------------------------|
| `http://127.0.0.1:9877/healthz`                    | tts-erp 健康检查                   |
| ~~`http://127.0.0.1:9877/shops`~~                  | ~~列出 shops（代理到 oauth）~~ **Wave 3 Slice 2 后已删除**（调 oauth_receiver_core.db_list_shops in-process） |
| `http://127.0.0.1:9877/token/<id>?reveal=1`         | 拿 token + cipher（同上，in-process） |
| `http://127.0.0.1:9877/sync/orders`                 | POST body {shop_id, ...}，从 TikTok 拉单入库 |
| `http://127.0.0.1:9877/orders/search?shop_id=X&page_size=10` | 直接代理到 TikTok（page_size 在 URL） |
| `http://127.0.0.1:9877/db/orders?shop_id=X&status=AWAITING_SHIPMENT` | 本地 DB 订单列表 |
| `http://127.0.0.1:9876/healthz`                    | oauth-receiver 健康检查           |
| `http://127.0.0.1:9876/token/<id>`                 | oauth-receiver 单个 shop token     |
| `http://127.0.0.1:9876/tokens/shops`               | oauth-receiver 所有 shops          |

## TikTok /orders/search 字段名踩坑实录

### 1. `page_size` 必须在 query string，不能在 body

**症状**：body `{"page_size": 10}` 返回 36009004 "PageSize is a required field"。

**根因**：TikTok 把 paging/sort 参数都放在 query string。Body 只能放过滤条件。

**已确认工作**（probe_alt.py 2026-08-16）：

```python
# 正确：
extra_params = {"page_size": "10", "sort_field": "create_time", "sort_order": "DESC"}
body = {"order_status": "100"}  # 或 None

# 错误（即便 body 里有 page_size）：
body = {"page_size": 10}  # → 36009004
```

### 2. `sort_order` 必须大写

小写 `desc` → 36009004 "SortOrder is invalid, allowed values: ASC,DESC."。修复：直接用大写 "DESC" / "ASC"。

### 3. `order_status` 在 body 里要是 **string**，不是 int

int 100 → 36009004 "param order_status type invalid. actual type:int64, expected type:string"。

### 4. `/orders/list` 不存在

返回 36009009 "Invalid path. The specified path does not match any available endpoint."。所有 list/query 走 `/orders/search`。

### 5. 202309 spec 的 status 是字符串

不是 int code，而是 `"AWAITING_SHIPMENT"` / `"UNPAID"` / `"IN_TRANSIT"` / `"DELIVERED"` / `"CANCELLED"` 之类。DB schema 已有 `order_status_name` TEXT 列专放这个。

### 6. 实际响应数据结构（实测 2026-08-16）

```json
{
  "code": 0,
  "data": {
    "next_page_token": "...",
    "orders": [
      {
        "id": "585574475916477491",
        "status": "AWAITING_SHIPMENT",
        "create_time": 1786870064,
        "update_time": 1786870565,
        "buyer_email": "v4bE...@scs2.tiktok.com",
        "fulfillment_type": "FULFILLMENT_BY_SELLER",
        "shipping_provider_id": "7439297584469903122",   // top-level
        "shipping_provider_name": "Wise Express - DCS",  // top-level
        "payment": {                                     // nested object
          "total_amount": "495548",
          "currency": "VND",
          ...
        },
        "line_items": [ ... ],
        "recipient_address": { ... }
      }
    ]
  }
}
```

`order_list` / `order_id` 都不存在！字段名是 `orders` / `id`。

### 7. Order 详情端点不在 path 里

**症状**：`GET /order/202309/orders/<id>` 返回 36009009 "Invalid path"。

**正确**：`GET /order/202309/orders?ids=<id>`（id 走 query string，且 `ids` 小写复数）。

实测支持多 id：`?ids=585574475916477491,585574340257089064`。

### 8. 202309 Order 模块是只读

实测 2026-08-16 全部下列端点都返回 36009009 "Invalid path"：

- `cancel` / `confirm` / `update_status` / `shipping_info` / `verify_shipping`（写）
- `tracking` / `risk` / `buyer` / `recipient` / `tracking/get`（读）

所以 `tts-erp` 路由层对 `/orders/<id>/{action}` 一律返回 **501 Not Implemented** + 友好提示，不静默转发避免暴露 36009009 给上游。

**真实路径**：TikTok 写操作在 Fulfillment (`/fulfillment/202309/...`) 和 Reverse Logistics (`/reverse/202309/...`) 模块，需要单独接。本次未实现。

### 9. /finance/202309 模块实测（2026-08-16 新增）

只接 list 端点，**sort_field 是 REQUIRED**（不传 → 36009004）。

```
GET /finance/202309/statements  →  data.statements[]    ✓
GET /finance/202309/payments    →  data.payments[]      ✓
```

全部 detail / sub-records / download 端点 36009009：

```
/finance/202309/statements/{id}                          → 36009009
/finance/202309/statements/{id}/transactions             → 36009009
/finance/202309/statements/{id}/orders                  → 36009009
/finance/202309/statements/{id}/refunds                 → 36009009
/finance/202309/statements/{id}/download                → 36009009
/finance/202309/transactions                             → 36009009
/finance/202309/settlements                              → 36009009
/finance/202309/balance                                 → 36009009
```

### 10. 2026-08-16 用户额外要求（已做 — return_refund/202309 集成）

**最新更新**（v1.3）：2026-08-16 晚，用户改回原意，要求接入 return_refund/202309 接口（reject/approve/accept 等高危写入**不接**）。完成情况：

**实际存在的端点**（probe_refund_v3/v5/v6 实测）：

- `POST /return_refund/202309/returns/search`        ✅ 列表，14 rows / 2 pages 已入库
- `POST /return_refund/202309/cancellations/search`  ✅ 列表，75 rows / 2 pages 已入库

**确认不存在的端点**（不接）：

- `/return_refund/202309/returns/{id}`              → 36009009 (no path)
- `/return_refund/202309/cancellations/{id}`        → 36009009 (no path)
- `/return_refund/202309/returns/list`              → 36009009
- `/reverse/202309/*` (所有 14 个变体)              → HTTP 404 (CDN-level, 模块在 202309 spec 中未开放)
- `/fulfillment/202309/*`                            → 36009009

**确认存在但不接的 WRITE 端点**（按用户要求 reject/approve/accept 等动作一律不接）：

- `POST /return_refund/202309/returns`              (CREATE return request — 需要 order_id + return_reason)
- `POST /return_refund/202309/cancellations`        (CREATE cancellation request — 需要 order_id)
- POST 详情子端点（`/returns/<id>/{approve,reject,cancel,seller_response,evidence_file,dispute}` 等）
- POST cancellation 子端点（`/cancellations/<id>/{approve,reject,accept,decline}` 等）
- POST reverse 子端点（`/orders/<id>/{approve,reject,cancel,confirm_receipt,ship,handle,respond,negotiate}` 等）

`tts-erp` 对 `POST /returns` 和 `POST /cancellations` 一律返 **501 Not Implemented** + 友好说明。

**Schema 新增**（schema.sql，2 张新表）：

```sql
CREATE TABLE returns (
    return_id TEXT PRIMARY KEY,    -- TikTok "return_id" 字段（不是 "id"！）
    shop_id, order_id, return_status, return_reason, return_type, role,
    create_time, update_time, raw JSONB, synced_at
);
CREATE TABLE cancellations (
    cancel_id TEXT PRIMARY KEY,    -- TikTok "cancel_id" 字段
    shop_id, order_id, cancel_status, cancel_reason, cancel_reason_text,
    cancel_type, role, should_replenish_stock,
    create_time, update_time, raw JSONB, synced_at
);
```

**新端点**（tts_erp v1.3）：

- `POST /returns/search`       body: `{shop_id, ...filters}` → 代理
- `POST /cancellations/search` body: `{shop_id, ...filters}` → 代理
- `POST /sync/returns`         body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}`
- `POST /sync/cancellations`   body: `{shop_id, create_time_ge?, create_time_lt?, page_size?}`
- `GET /db/returns?shop_id=&status=&limit=`
- `GET /db/cancellations?shop_id=&status=&limit=`

**字段名关键差异**（与 finance 模式不同）：

- `returns.search` 响应数组在 `data.return_orders`，主键是 `return_id`（不是 `id`！）
- `cancellations.search` 响应数组在 `data.cancellations`，主键是 `cancel_id`（不是 `id`）
- paging 走 query string（page_size/sort_field/sort_order），body 只放过滤条件
- `paging` 用 `next_page_token`（与 finance 一致）
- `cancel_status` 实际是 "CANCELLATION_REQUEST_COMPLETE"（自动完成的取消，buyer 主动取消会立即 complete）
- `return_status` 可能是 "AWAITING_BUYER_SHIP" / "BUYER_SHIPPED_ITEM" / "RETURN_OR_REFUND_REQUEST_COMPLETE"

**page_size 限制**（实测踩坑）：`/return_refund/202309/*/search` 端点的 `page_size` 范围是 **10-50**，
超过 50 返 `98001004 "Value Out Of Range"`。代码里 `min(max(page_size, 10), 50)` 自动夹紧。
注意：order / finance 端点的上限是 100（**不能**用 50 当默认值，TikTok 会觉得"用得不够"）。

**取消 vs 退货的业务差异**：

- 取消（cancellations）：发货前的 order 取消，由 buyer/seller 主动发起。所有 75 条记录都是 `CANCELLATION_REQUEST_COMPLETE`。
- 退货（returns）：发货后 buyer 申请退款/退货，需要走物流寄回 + 平台审核。14 条记录分布：
  - 11 `RETURN_OR_REFUND_REQUEST_COMPLETE`（仅退款，无需退货）
  - 2 `AWAITING_BUYER_SHIP`（等买家寄回）
  - 1 `BUYER_SHIPPED_ITEM`（买家已寄回，等收货）

## 还没完成 / 已知问题

### 1. 没接 webhook

TikTok Shop 支持 order 状态变化的 webhook 推送（order.created, order.paid, order.shipped 等）。
这需要公网回调（已有 cpolar 隧道）+ TikTok webhook 签名验证 + 入库逻辑。

**怎么开始**：

- 在 tts-erp 加 `POST /webhook/tiktok` 端点
- 用 `TIKTOK_APP_SECRET` 验证 `x-tts-signature` header
- 根据事件类型更新 `orders` 表
- 需要在 Partner Center 配置 webhook URL（用 cpolar 域名）

**预估时间**：1-2 小时

### 2. 没做增量同步

现在 `/sync/orders` 是全量翻页拉，page_size 上限 100。生产应该用：

```bash
# 第一次全量
curl -X POST http://127.0.0.1:9877/sync/orders \
  -d '{"shop_id": "X", "page_size": 100}'

# 之后增量（每 N 小时跑一次）
curl -X POST http://127.0.0.1:9877/sync/orders \
  -d '{"shop_id": "X", "create_time_ge": <上次同步时间>, "page_size": 100}'
```

**怎么开始**：

- 加 `last_synced_at` 时间戳到 `shops` 表
- 写个简单的 cron 调 `/sync/orders` 用 `create_time_ge`
- 或者直接在 `tts-erp` 加个 `/sync/incremental` 端点

**预估时间**：30 分钟

### 3. app_secret 暴露在聊天记录

⚠️ **app_secret 已经在多个聊天记录里出现过**。强烈建议在所有功能验证后**去 TikTok Partner Center 重置一次**。同时：

- 重置后要更新 `/home/schan/oauth-receiver/.env` 和 `/home/schan/tts-erp/.env`
- 因为 access_token 是按 app_secret+access_token 算的，app_secret 一变 → 必须全部重新走 OAuth 授权流（用 cpolar 走 `/authorize`）

### 4. DB 密码暴露

⚠️ `==REDACTED_DB_PASS==` 也在多个聊天记录里。同样建议重置。

## 调试历史（给下个 session 的备忘）

### HMAC 签名踩过的坑

1. **位置错位 (positional args)**：`build_signed_url(api_host, path, app_key, app_secret, extra_params, body, timeout)` 第 6 个是 `body` 不是 `timeout`。调用时 positional 传 `timeout` 到了 `body` 位置，导致 `canonical += 30`（int）。**修复**：全部用 keyword args。
2. **Pattern 选错**：
   - `{secret}{path}{kv}{secret}` ❌ (106001)
   - `{secret}{path}{kv}{secret}{body}` ❌ (106001)
   - `{secret}{path}{kv}{body}{secret}` ✅ (通过！)
3. **body SHA256 哈希**：试过 SHA256(body) 也不行，必须是 raw JSON
4. **URL encoding**：不要对 body 做 URL encoding，原 JSON 字符串

最终 canonical（实测通过）：

```
c90503.../order/202309/orders/searchapp_key==REDACTED_APP_KEY==shop_cipherROW_...Entimestamp1786875581{"page_size": 10}c90503...
```

### 启动服务的坑

1. **bash 转义地狱**：Windows PowerShell + Git Bash 调 `nohup ... &` 加 `disown` 经常 launch 失败
2. **env vars 没传进子 shell**：`TTS_DEBUG_SIGN=1` 写在前面但 `set -a; . .env` 在后面 → app_secret 为空
3. **解决方法**：写个 `start.py` 显式读 .env 然后 `subprocess.Popen(env=os.environ)`，比 bash 可靠
4. **最终方案**：只保留 `restart.sh`（之前还备 `start.py` 兜底，但 `restart.sh` 工作稳定后 `start.py` 已删）

**推荐启动方式**：

```bash
bash /home/schan/tts-erp/restart.sh
```

## 下个 session 优先做

1. **重置 app_secret 和 DB 密码**（安全第一）
2. 增量同步（实用）
3. 接 webhook（实用）
4. 加 `/healthz` 返回 DB 状态 + oauth-receiver 连通性
5. 把 tts-erp 跟 oauth-receiver 一起做 systemd service（需要 sudo 密码）
6. **如果用户后续要接 reject/approve 等动作**，需要单独 review 并加 confirm 双确认（这些动作会真改 TikTok 状态）
7. **如果用户后续要接 `/reverse/202309/*`**，需要先确认 TikTok 是否在 202309 中开放了 reverse logistics（目前 CDN 404）；可能要切到 fulfillment 模块

## 配置快照

```
OAUTH_RECEIVER_URL  http://127.0.0.1:9876
TIKTOK_APP_KEY      ==REDACTED_APP_KEY==
TIKTOK_APP_SECRET   ==REDACTED_APP_SECRET==  ← 重置
TIKTOK_API_HOST     https://open-api.tiktokglobalshop.com
TTS_ERP_DB_URL      postgresql://postgres:==REDACTED_DB_PASS==@127.0.0.1:5432/tts_erp  ← 改密码
TIKTOK_REDIRECT_URI http://daqiang.nat100.top/callback   (in oauth-receiver)
```

⚠️ **app_secret 和 DB 密码都已经在多个聊天记录里出现过**。强烈建议在所有功能验证后**去 TikTok Partner Center + Postgres 各自重置一次**。

## 关键命令

```bash
# 看 oauth-receiver 状态
ssh -i "C:\Users\chen\Desktop\keys\192.168.47.130@schan.txt" schan@192.168.47.130 "curl -s http://127.0.0.1:9876/healthz | python3 -m json.tool"

# 看 tts-erp 状态
ssh -i "C:\Users\chen\Desktop\keys\192.168.47.130@schan.txt" schan@192.168.47.130 "curl -s http://127.0.0.1:9877/healthz | python3 -m json.tool"

# 重启两个服务
ssh -i "C:\Users\chen\Desktop\keys\192.168.47.130@schan.txt" schan@192.168.47.130 "bash /home/schan/oauth-receiver/restart.sh && bash /home/schan/tts-erp/restart.sh"

# 端到端冒烟测试
scp "F:\MiniMax Work Result\tts-erp\test_e2e.py" schan@192.168.47.130:/tmp/test_tts_erp.py
ssh -i "..." schan@192.168.47.130 "set -a; source /home/schan/tts-erp/.env; set +a; python3 /tmp/test_tts_erp.py"

# 同步所有模块
for mod in orders statements payments returns cancellations; do
  curl -X POST "http://127.0.0.1:9877/sync/$mod" -H "Content-Type: application/json" -d '{"shop_id":"7494763368967603447","page_size":100}'
done

# 查 DB 里的退货/取消
ssh -i "..." schan@192.168.47.130 "curl -s 'http://127.0.0.1:9877/db/returns?shop_id=7494763368967603447&limit=5'"
ssh -i "..." schan@192.168.47.130 "curl -s 'http://127.0.0.1:9877/db/cancellations?shop_id=7494763368967603447&limit=5'"

# 看 cron 续期日志
ssh -i "..." schan@192.168.47.130 "tail -30 /home/schan/oauth-receiver/logs/cron-refresh.log"
```

## 2026-08-27 — Wave 4.1 (legacy retirement)

- 5 个 Handler `_sync_miaoushou_*` / `_db_list_miaoushou_*` 抽到 `tdd/miaoshou_sync.py` 模块级函数（返回 (code, body)）。
- `_invoke_legacy_sync` 改为直接调模块函数（getattr 兑底 501）。
- `tts_erp.py` 删了 Handler 类 + main() + _proxy_get()：3512 → 1691 行。文件现在是共享 helper 模块。
- AGENTS.md L189 / L371 同步更新。
- 剩余 follow-up（独立 scope）：`do_POST` 里 `from miaoshou.callbacks.router import dispatch_callback` + `_miaoshou_call_endpoint` 仍是 dead 状态。

  **2026-08-27 后续（其他 session）**: 删了 `do_POST` 里的 `from miaoshou.callbacks.router import dispatch_callback` + `_miaoshou_call_endpoint`（160 行 dead code），并删了测该 dead code 的 `test_handler_routing.py`；AGENTS.md L371 同步标记为已完成。
