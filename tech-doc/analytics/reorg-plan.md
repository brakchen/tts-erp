# Analytics Schema Reorg — 技术方案（reorg-plan）

> 状态: **已确认决策，待实施**（本文件为正式执行依据；决策记录见
> context-mode `analytics-reorg-decisions`，2026-09-05）
> 关联文档: `tech-doc/analytics/dump-architecture.md`（现协议）、
> `tech-doc/data-model-target-v3.md`（§14 数据规范）、`AGENTS.md`
> 实施方式: 独立 worktree lane（`feature/analytics-schema-reorg`），代码 + migration
>
> + 测试全绿后 merge；**DB apply / regen / 重启属部署步骤，另走上线清单**。

## 1. 目标（一句话）

把 `analytics` schema 从「5 表 + 1 view，其中 3 张写放大僵尸表 + 1 张 DB 审计表」收成
「1 张源表（`ad_raw`）+ 1 个 view（`ad_product_links`，不变）+ 结构化文件日志」，
清掉所有死代码与已失去存在意义的 retention job；**对外协议（/dumps、/cursor has-data）
与 Chrome 扩展完全不动**。

## 2. 决策记录（用户已确认，直接执行）

| # | 决策 | 依据（code facts） |
| --- | --- | --- |
| 1 | 删除 `analytics.ad_daily_completeness` | dump-architecture D3 后 has-data 直接查 ad_raw；该表不参与协议、无生产读方，仅每次 dump 第三次写放大（UPSERT captured_at）。 |
| 2 | 删除 `analytics.ad_records` | 生产代码零 SELECT（仅 INSERT + retention 90d DELETE；读取只出现在 migration 回填与测试）；与 ad_raw.response.body 重复存同一 payload。 |
| 3 | 删除 `analytics.ad_shop_timezones` | 生产读写路径均死：`fetch_timezone()` 仅测试调用、`SQL_UPSERT_TIMEZONE`/`SQL_GET_TIMEZONE`/`SQL_SEED_TIMEZONE`/`SQL_REPAIR_TIMEZONE` 均为未执行死 SQL、`_today_in_tz()` 无调用方；11 行是 0005 前 v1 cursor 协议遗留（值全为默认 Asia/Shanghai）。day 由 plugin 自报，server 不换算时区。 |
| 4 | 删除 `analytics.ad_audit_log`，**改为结构化文件日志** | 审计是日志不是数据：生产零 SELECT（仅 retention DELETE + 人肉 SQL + 测试断言）；失败路径 `_audit_and_error` 已写 stderr；与 `middleware/access_log.py`（全站每请求一行 stdout.log）重叠。改为 logger 单行 key=value，成功路径也补一行（现唯一丢失信息 = 成功请求的 records 计数）。 |

范围边界（本轮 **不做**，另记未决）：

+ `ad_raw` 保持现状（upsert 语义、5 元组 unique、/dumps 协议）——append-only 改造是后续话题；
+ typed 分析事实表（`ad_product_daily` 等，解决「指标埋在 jsonb + 视图每次现算」）另立方案，不进本轮；
+ `ad_product_links` VIEW 不变（只读 ad_raw，天然兼容）；
+ Chrome 扩展协议不变 → 扩展无需发布。

## 3. 现状（改动前事实基线）

现网行数：ad_raw 5,864 / ad_records 5,864 / ad_daily_completeness 5,864 /
ad_shop_timezones 11 / ad_audit_log 54,786。全仓唯一读路径 = `ad_product_links` VIEW +
/cursor has-data（查 ad_raw）。

## 4. 目标形态

```text
analytics schema（重排后）
├── ad_raw                     -- 源表（唯一落库表，保留）
├── ad_product_links (VIEW)    -- 不变
└── (ad_audit_log 的职责)      -- 改为 tts_erp_v2.analytics logger 单行日志

被删表：ad_records / ad_daily_completeness / ad_shop_timezones / ad_audit_log
被删 job：analytics.retention（JOBS 摘除 + 文件删除；ad_records/audit 都没了，无存在意义）
```

## 5. 改动清单（按文件，含精确位置）

### 5.1 alembic migration（新增 `alembic/versions/0007_analytics_reorg_drop_dead_tables.py`）

+ 命名与风格参照现有 `0005_ad_raw_per_unit_day.py` / `0006_ad_product_links_view.py`；
  当前 head = 0006，新版本号 **0007**。
+ upgrade：`op.drop_table`（或等价的 raw SQL，风格跟随 0005/0006）依次 drop：
  `analytics.ad_audit_log` → `analytics.ad_shop_timezones` →
  `analytics.ad_daily_completeness` → `analytics.ad_records`（先 drop 无 FK 依赖的表；
  本组表无跨表 FK，顺序不敏感，仍按依赖直觉排）。索引随表 drop 自动消失。
+ downgrade：重建 4 张表（列定义照抄现 models/`schema_tts_erp.sql` 里的旧定义），
  **注释声明：ad_raw 仍可重建 ad_records/ad_daily_completeness；ad_audit_log 历史
  数据不可恢复（已接受，见 §7 风险）**。down 只保证 schema 可回滚，不保证数据。
+ 文件头 docstring：写清动机 + 关联决策 #1-4 + 关联文档。

### 5.2 `tts_erp_v2/db/models/analytics.py`

+ 删除 4 个类：`AdRecord` / `AdDailyCompleteness` / `AdShopTimezone` / `AdAuditLog`；
  保留 `AdRaw`。
+ 模块 docstring 重写：5 表 → 1 表 + view 的说明；删除 `_STORAGE_KEY_CHECK` 中
  仅被已删类引用的部分（若 `AdRaw` 不再用则一并删，注意别删 AdRaw 仍依赖的常量）。
+ 确认 `tts_erp_v2/db/models/__init__.py` 是否显式聚合类名，若有则同步删。

### 5.3 `tts_erp_v2/analytics/repository.py`（核心瘦身）

+ 删 SQL 常量（模块级死 SQL）：
  `SQL_INSERT_RECORD_DERIVED` / `SQL_UPSERT_DAILY_COMPLETENESS_CAPTURED` /
  `SQL_INSERT_AUDIT` / `SQL_PURGE_RECORDS` / `SQL_PURGE_AUDIT` /
  `SQL_GET_TIMEZONE` / `SQL_SEED_TIMEZONE` / `SQL_REPAIR_TIMEZONE` /
  `SQL_UPSERT_TIMEZONE`。
+ 删函数：`write_audit()` / `purge_expired()` / `fetch_timezone()`。
+ `upsert_dump()`：**缩为单表写**——只 INSERT ad_raw（ON CONFLICT 5 元组 DO UPDATE，
  RETURNING xmax=0 判 inserted/duplicate），去掉 ad_records / ad_daily_completeness /
  timezone 三个步骤；docstring 同步改（1 表 1 事务）。
+ 保留：`SQL_INSERT_RAW` / `SQL_HAS_DATA` / `has_data()` 及日期工具
  （`_add_days`/`_subtract_days` 若无人用则删——grep 确认，`has_data` 不用它们）。
+ 清理因上述删除而变成孤儿 import（`sys`/`Engine`/`get_engine` 等，看删除后实际残留）。
+ 模块 docstring 顶部「谁写谁读」说明同步更新。

### 5.4 `tts_erp_v2/analytics/domain.py`

+ 若 `DEFAULT_TIMEZONE` 只剩 `_today_in_tz()` 在用 → 两者一并删（见 5.5）；
  若他处仍引用先 grep 确认。`DumpPayload`/`HasDataResult` 等协议 dataclass 不动。

### 5.5 `tts_erp_v2/api/v2/analytics.py`（审计 → 文件日志）

+ **目标**：删掉全部 `write_audit(...)` 调用与 import；错误与成功路径都改为
  结构化 logger 单行。保留既有 `_audit_and_error` 的 stderr 诊断（已工作），但把
  「再写一条 DB」的 write_audit 换成「再打一条结构化 log」。
+ 新增模块级 logger：`log = logging.getLogger("tts_erp_v2.analytics.ingest")`
  （仓库日志约定见 `middleware/access_log.py`：logger → systemd stderr/stdout 文件，
  参考其 handler 说明，勿自己加 StreamHandler 之外的配置）。
+ 单行格式（key=value，换行压平，≤500 字符，消毒规则沿用 `_sanitize_pydantic_errors`
  与 `safe_message`）：`ts / level / request_id / key_prefix / method / path /
  status / records_in / records_ok / records_rej / error_code / message`。
+ 改动点：
  1. `get_cursor`（has-data）：scope 拒绝（403）路径、400 SCHEMA_INVALID 路径、200 成功
     路径 → 各打一条 log（成功路径带 records_in=1 / records_ok=0|1）；
  2. `post_dumps`：成功（inserted/duplicate）与 `_audit_and_error` 各错误分支 → log；
     records 计数照抄现有 write_audit 实参；
  3. `_audit_and_error`：保留 stderr 行，其内 write_audit 调用改为 log；
  4. 删除 import 列表里的 `write_audit`；`DEFAULT_TIMEZONE` + `_today_in_tz()` +
     `ZoneInfo` import（若 5.4 判定无他用）一并删；
  5. `_request_id_from_headers` / `_key_prefix` 保留（log 要用）。
+ **响应契约不变**：/cursor has-data JSON、/dumps 响应（code/requestId/data）逐字节不动；
  tests/api contract 测试除 audit 断言外应全绿不改语义。

### 5.6 `tts_erp_v2/jobs/analytics_retention.py`

+ 整个文件删除（含 `JOB_NAME = "analytics.retention"`、`run_analytics_retention`、
  `_env_int`、module docstring）。

### 5.7 `tts_erp_v2/sync_worker/scheduler.py`

+ `JOBS` dict 删除 `"analytics.retention"` 条目及其上方注释段（约 L164-171，含
  「Analytics retention」注释）；若有 `JobSpec` 引用 retention module 一并清。
+ 检查 `job_runner.py` / `main.py` 是否硬编码 job 名清单（grep `analytics.retention`）。

### 5.8 schema 与 regen

+ `schema_tts_erp.sql` / `schema_oauth.sql` 由 `scripts/regen_schema.py` 从已 apply
  migration 的真库重新生成——**regen 属部署步骤**（DB 未 apply 前 regen 会带回旧表）。
  本 lane 不改 schema SQL 文件；部署清单（§9）负责 apply 后 regen + commit。

### 5.9 测试（TDD：先改测试表达新预期）

全仓 grep `ad_records|ad_daily_completeness|ad_shop_timezones|ad_audit_log|write_audit|
fetch_timezone|purge_expired|analytics.retention|analytics_retention`（tests/ 内）逐一处理：

+ `tests/analytics/test_repository.py`：删 fetch_timezone 3 测试、purge/audit 相关；
  保留 ad_raw upsert（xmax inserted/duplicate）、has_data 测试；文件级 TEST_ 清理语句
  若 DELETE 旧表则删（表 drop 后不存在）。upsert_dump 测试改为断言只写 ad_raw 一张
  （INSERT 后 `SELECT count(*) FROM analytics.ad_raw WHERE ...`=1，其余旧表不产生行——
  在 DB 未 apply 0007 时旧表仍在，正好断言「新代码不再写它们」）。
+ `tests/api/test_analytics_v2_contract.py`：`test_v2_dumps_audit_log_written` →
  caplog 断言出现 ingest log 行（endpoint=dumps、records_in/ok 正确）；L40 等 autouse
  `DELETE FROM analytics.ad_audit_log ...` 清理语句删/改（若 fixture 级清理在删，同步）；
  其余 has-data / 幂等 / 2MB 上限测试不动。
+ `tests/api/test_analytics_v2_errors.py`：`_cleanup_audit_rows` 删；audit 行断言
  （如 L313 `test_schema_invalid_persists_error_message_in_audit_log`）改为 caplog /
  捕获 stderr 断言消毒 message；stderr 行断言保留（_audit_and_error 仍写 stderr）。
+ `tests/sync_worker/test_analytics_retention.py`：整文件删。
+ `tests/sync_worker/test_scheduler_jobs_coverage.py`：`EXPECTED_JOB_INTERVALS` /
  JOBS 完整性断言删 `analytics.retention` 行（先 grep 实际位置）。
+ `tests/sync_worker/test_main.py`：grep 后清理对 retention 的引用。
+ `tests/sync_worker/test_same_value_bumps_updated_at.py` / 其他：只清真正引用被删表的
  语句，**不要碰他人 lane 未提交内容**（master 工作区现有他人 WIP 文件列表见 §10，
  lane 从 master HEAD 分支，天然不包含它们，勿 add 它们）。

### 5.10 文档（lane 内可提交的）

+ `tech-doc/analytics/dump-architecture.md`：§3.1 数据流图（3 表事务 → 1 表）、
  §4 决策表、§9/§10 引用处同步；加一句指向本 reorg-plan。
+ `AGENTS.md`：若 §8 目录地图或 §9.5 提到 5 张表 / analytics.retention，同步改
  （grep `ad_records|analytics.retention|ad_audit` 确认）。
+ `setup/analytics-sync.md`：grep retention/audit 说明并更新（如提及）。
+ `CHANGELOG.md`：加条目（docs，版本号跟随仓库现约定，只描述不 bump）。
+ `tech-doc/analytics/ad-product-links-ui.md` / `biz-doc/analytics/*`：只读 ad_raw/view，
  无引用被删表则不动。
+ 本文件实施完勾状态（头部状态行 + §9 checklist）。

## 6. 不做 / 未决（明确排除，防范围蔓延）

1. ad_raw append-only 语义 / 覆盖审计改造（P5）——未决，另案；
2. typed 事实表（P3，把 product/session/changeLog 从 jsonb 抽成关系列）——未决，另案；
3. `ad_product_links` VIEW 改为基于事实表或加窗口参数——跟随 2；
4. Chrome 扩展 / /v2/analytics/sync 协议变更——本轮不动；
5. audit 的「降噪（只落可疑行）」或产品化只读端点——audit 已改日志，不再需要；
6. 现有 5,864 行 ad_raw 数据清洗——不在此范围。

## 7. 风险与数据影响

| 风险 | 评估 |
| --- | --- |
| 数据丢失 | **无业务数据丢失**：被删 4 表均可重建或已无价值——ad_records/ad_daily_completeness 可自 ad_raw 重派生（保留期也只是临时拷贝）；ad_shop_timezones 11 行全为默认值；ad_audit_log 54,786 行历史**随 drop 丢失（已接受）**——如需留底，部署时先 `SELECT` 导出到日志文件再 apply（可选，默认不导）。 |
| 代码与 DB 不同步 | 老代码（未上线前）若遇 DB 已 drop → write_audit best-effort 只打 stderr、upsert 若引用旧表会报错——因此**必须代码 + migration 同窗口上线**（§9 顺序不可颠倒）。 |
| retention job 空转 | JOBS 摘除后无残留调度；若漏摘，job 每日 DELETE 报错进 sync_issues——上线清单里核对无 `analytics.retention` 日志。 |
| 测试与环境 | conftest 的 `_check_schema_prereq` 要求 alembic head 已 apply 才跑域测试——本 lane **不 apply 0007**，新代码在旧表仍存在的 DB 上必须全绿（新代码本就不写旧表），apply 后同一套测试继续绿。 |
| 他人 lane 冲突 | master 现有他人未提交改动（§10 列表）——lane 不 touch、不 add、不 commit 它们；merge 前若冲突按 AGENTS §11 rebase 规则处理。 |

## 8. 实施步骤（lane 内执行顺序）

1. `git worktree add .worktrees/analytics-schema-reorg -b feature/analytics-schema-reorg`；
2. 先改测试（5.9）→ 跑相关域测试见红（红点 = 旧表写入/audit 断言仍在）→ 再改实现
   （5.1-5.7）→ 全绿；
3. 域测试：`.venv/bin/pytest tests/analytics tests/api/test_analytics_v2_contract.py
   tests/api/test_analytics_v2_errors.py tests/sync_worker -q`；
4. 门禁：`bash scripts/test.sh fast` **0 fail**（唯一出口标准）；
5. 文档（5.10）；git commit（本地，不 push；commit message 带类型前缀 + 中文，
   如 `feat(schema): analytics reorg — drop 4 dead tables + audit→file log`，可拆多 commit）；
6. 回报：changed files 清单 + 测试结果 + 未决项。

## 9. 上线清单（lane 之外，merge/review 后由部署方执行）

+ [ ] review lane diff（重点：upsert_dump 单表化、日志行格式、测试是否真删干净）
+ [ ] master merge（--no-ff）+ push
+ [ ] `bash scripts/test.sh fast` 0 fail（master 上再跑一次）
+ [ ] 部署窗口：`.venv/bin/alembic upgrade head`（apply 0007，drop 4 表；先确认无
      running job 正在写）
+ [ ] `python3 scripts/regen_schema.py` 重新生成 schema SQL 并 commit（schema_tts_erp.sql）
+ [ ] `systemctl --user restart tts-erp.service` + `systemctl --user restart tts-erp-sync.service`
+ [ ] 冒烟：healthz；`GET /v2/analytics/sync/cursor` has-data 行为不变；`POST /v2/analytics/sync/dumps`
      一次 → ad_raw +1 行、无旧表写报错、日志出现 ingest 行（stderr/stdout 文件）
+ [ ] 24h 观察：sync_issues 无新 `analytics.retention` 报错；logs/stderr.log 无
      `[analytics-sync] audit write failed`
+ [ ] 文档收尾：dump-architecture.md / AGENTS.md / CHANGELOG 已随 lane 或此处补齐

## 10. 附：实施时不要碰的他人 WIP（master 工作区现状，2026-09-05）

+ 已修改未提交：`tests/analytics/test_ad_product_links_view.py`、
  `tests/sync_worker/test_same_value_bumps_updated_at.py`、`tts_erp_v2/sync_worker/watermarks.py`
+ 未跟踪：`reports/`、`scripts/oneoff_ad_raw_report.py`、
  `tech-doc/analytics/ad-product-links-ui.md`、`tts-partner-api-docs/`
+ 活跃 worktree：`.worktrees/channel-account-by-external`（feature/channel-account-by-external）
