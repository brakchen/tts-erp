# 2026-09-13 prod plugin.ad_daily purge — P0 incident

## TL;DR

| 项 | 值 |
| --- | --- |
| 严重度 | **P0** — prod `plugin.ad_daily` 被清空 ~98% |
| 触发时间 | 2026-09-13 **08:19:22 UTC**（北京时间 16:19） |
| 发现时间 | 2026-09-13 16:30 左右（用户看 ROI 页面发现数据异常） |
| 恢复完成 | 2026-09-13 17:25 UTC |
| 数据丢失 | prod `plugin.ad_daily` 从 **14,719 行 → 1,036 行**（丢失 ~13,683 行） |
| 涉及维度 | 246 campaigns → 18 campaigns；111 products → 13 products；65 days → 64 days |
| 真凶 | `tests/api/test_admin_purge.py::test_purge_plugin_data_clears_ad_tables` 在 prod 库运行 |
| 恢复方式 | 06:00 preserved pgdump（14,306 行）+ 9-13 早上 chrome backfill 残骸（1,131 行）= **15,437 行恢复** |

## 时间线（PG log UTC 时间）

| 时间 (UTC) | 事件 |
| --- | --- |
| 04:00 | ad_daily = **11,881 行** / 111 products / 246 campaigns — pgdump 自动备份 |
| 06:00 | ad_daily = 14,306 行（同 111/246） — pgdump，merge_all_tables.sh 用的源 |
| 07:14 | ad_daily = 14,719 行（仍然 111/246） — 人工触发备份（pgbackup.log 这次没走 ROTATE） |
| **08:19:17.467** | session 1537 第 1 段事务：8 条 `_wipe_test_rows`（TEST_ 清理）— SQLAlchemy 测试 session 在 prod 库跑 |
| **08:19:22.685** | session 1537 第 2 段事务：`DELETE FROM plugin.ad_daily` 裸 DELETE（清空 14,719 行） + `DELETE FROM plugin.ad_raw_log` — **指纹完全匹配 `tts_erp_v2/api/v2/admin.py::purge_plugin_data`** |
| 08:19:25.252 | 紧接着 INSERT 测试数据：seller_id='8800000000000000002'（test_admin_shops.py 的 SHOP_B） |
| 09:00 | ad_daily = **1,036 行** / 13 products / 18 campaigns — pgdump（删除后状态） |
| 现在 | ad_daily = **15,437 行**（恢复后） |

## 根因分析（5-Why）

### Why 1: prod `plugin.ad_daily` 为什么被清空了？
有人在 prod tts_erp 库执行了 `DELETE FROM plugin.ad_daily`（裸 DELETE，无 WHERE）。

### Why 2: 谁触发的？
PG log session 1537 的语句序列**完美对应** `tests/api/test_admin_purge.py::test_purge_plugin_data_clears_ad_tables`：
1. 8 条 `_wipe_test_rows`（TEST_ 清理，session-end cleanup）
2. 5 秒后 `BEGIN` 第二个事务，11 个 `SELECT COUNT(*)` + 2 个 `DELETE` —— 与 `purge_plugin_data` 端点的实现逐行匹配
3. INSERT 测试数据（seller_id='8800000000000000002'）

### Why 3: 为什么测试在 prod 库运行？
worktree `.env` 是软链到主仓的 prod `.env`（`TTS_ERP_DB_URL=...tts_erp`），而 worktree 内没有 `.env.test`。裸跑 `pytest tests/api/test_admin_purge.py` 时 conftest 只打 WARNING（"tests will run against the production-shaped DB"），**没有 fail-fast**。

### Why 4: 为什么 conftest 当时只是 warning？
2026-09-07 加测试库隔离时（commit 31b1a3c 系列），`tests/conftest.py` 选择**软警告**而非硬 fail：
> "We do NOT hard-fail when TTS_ERP_DB_URL_TEST is unset: that keeps direct pytest invocations (e.g. pytest tests/db/ for a one-off introspection) working."

理由是允许开发者在没有 `.env.test` 时做"one-off introspection"——但**这等于在 prod 库上跑测试无任何额外保护**。

### Why 5: 为什么这次不是 one-off introspection 而是完整 purge？
`test_admin_purge.py::test_purge_plugin_data_clears_ad_tables` 的测试函数体调用 `api_client.post("/v2/admin/purge-plugin-data", headers={"Authorization": f"Bearer {admin_key}"})` —— 它在 prod 库**完整执行**了端点逻辑，包括 DELETE。

更深一层：**`bfb6b71` 把 purge 权限从 admin 降到 readwrite**，又**没有 dry-run 守卫、没有 confirm 二次确认**，让 readwrite key 拿到端点直接清库成为可能。

## 为什么 HTTP access log 里没有记录？

`stdout.log` 显示北京时间 16:17~16:23 段（=UTC 08:17~08:23）的访问日志里**没有 `/v2/admin/purge-plugin-data` 请求**。

解释：**测试不走 HTTP 路径**。session 1537 第一个 BEGIN 紧跟 `SAVEPOINT "_pg3_1"`（SQLAlchemy ORM 模式），说明 pytest 的 fixture 在内部直接调 `purge_plugin_data(request=...)` 函数（不走 FastAPI 路由）。HTTP access log 自然没有这一笔。

## 防护加固（已落地，commit fix/recover-ad-daily-purge-guard）

### 1. `tests/conftest.py` — 硬 fail on prod-shape dbname

把 WARNING 升级为 `pytest.exit(returncode=2)`：
- prod dbname（`tts_erp` / `tts_erp_prod` / `tts_erp_prod_*`）→ **直接 fail-fast**
- 仅当 `TTS_ERP_TEST_OFF=1` 才允许跑（同时 stderr 打醒目 banner）

### 2. `tts_erp_v2/api/v2/admin.py::purge_plugin_data` — 两道 gate

- **Gate 1: prod-shape dbname 守卫**：内部函数 `_is_prod_shaped_db()` 检查 `TTS_ERP_DB_URL` 解析出的 dbname，prod-shape 时返回 403 + 明确拒因；只有 `ALLOW_PROD_PURGE=1` 或 (`allow_prod=true` query param 且 `TTS_ERP_ENVIRONMENT=dev`) 才放行
- **Gate 2: dry-run by default**：必须 `?confirm=true` 才真删；无 confirm 时返回行数 + `dry_run: true` + `next_step` 提示
- **role 复位**：`bfb6b71` 降到 readwrite 改回 admin

### 3. `tests/api/test_admin_purge.py` — 测试适配双 gate

- `test_purge_plugin_data_requires_admin` 改为 `?confirm=true` 后 readonly 测（之前测的是未带 confirm）
- 新增 `test_purge_plugin_data_dry_run_does_not_delete`：验证 dry-run 计数不删
- `test_purge_plugin_data_clears_ad_tables` 加 `?confirm=true` 真正清

## 恢复操作记录

```python
# Step 1: 备份当前 prod 残骸（1,138 行）到 plugin.ad_daily_rescue_20260913
CREATE TABLE plugin.ad_daily_rescue_20260913 AS SELECT * FROM plugin.ad_daily;

# Step 2: 从 06:00 preserved pgdump 抠出 ad_daily COPY 段
zcat /home/schan/backups/tts_erp_pgdump_preserved/tts_erp_20260913_060023.sql.gz \
  | awk '/^COPY plugin\.ad_daily / {f=1; print; next} f && /^\\\.$/ {print; exit} f' \
  > /tmp/ad_daily_copy.tsv
# → 14,306 行

# Step 3: 建 staging 表 + COPY 灌入
CREATE TABLE plugin.ad_daily_restore_staging (LIKE plugin.ad_daily INCLUDING DEFAULTS);
COPY plugin.ad_daily_restore_staging (...) FROM '/tmp/ad_daily_copy.tsv';

# Step 4: 把 prod 残骸里 staging 没有的 (camp,prod,day) 三元组备份到 extra
# → 1,131 行（9-13 早上 chrome backfill 写入的 20 个新 campaigns × 65 天）

# Step 5: TRUNCATE + INSERT FROM staging + INSERT FROM extra
TRUNCATE plugin.ad_daily RESTART IDENTITY;
INSERT INTO plugin.ad_daily (...) OVERRIDING SYSTEM VALUE SELECT ... FROM staging;
INSERT INTO plugin.ad_daily (...) OVERRIDING SYSTEM VALUE SELECT ... FROM extra;

# Step 6: 修复 ad_daily_id_seq
SELECT setval('plugin.ad_daily_id_seq', (SELECT max(id) FROM plugin.ad_daily), true);
# → 59,500 (next=59,501)
```

恢复后：

| 维度 | 删前 (06:00) | 删后 | 恢复后 |
| --- | --- | --- | --- |
| rows | 14,306 | 1,036 | **15,437** ✓ |
| products | 111 | 13 | **111** ✓ |
| campaigns | 246 | 18 | **246** ✓ |
| days | 65 | 64 | **65** ✓ |
| 总成本 (¥) | — | — | 6,389.59 |

**比删除前还多 1,131 行**——保留了 9-13 早上 chrome plugin backfill 写入的 20 个新 campaigns × 65 天的数据。

## 反思（教训）

1. **WARNING 不够 loud** — conftest 的软警告机制被无视；改成 hard fail 是 P0 必修课
2. **DESTRUCTIVE 端点不能没二次确认** — `purge_plugin_data` 这种删表端点，**必须**有 confirm query param / dry-run / role gate 三件套
3. **prod-shape dbname 识别要严格** — `_is_prod_shaped_db()` 现在覆盖 `tts_erp`、`tts_erp_prod`、`tts_erp_prod_*` 前缀，并 fail-closed（URL 解析失败 / 未设环境变量都拒绝）
4. **测试套件的"introspection"豁免机制风险** — 历史上为了"one-off pytest"留的口子，是事故的温床
5. **HTTP access log 不可作为唯一审计源** — 直接 import 调函数不走 HTTP，绕开所有 access log；要靠 PG log + 数据库自身审计
