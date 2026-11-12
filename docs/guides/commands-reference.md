# tts-erp 命令参考

> 本文档包含 tts-erp 项目的常用命令和测试规范。
> 通用约束和边界规则见 `AGENTS.md`。

## 1. 测试命令

```bash
# DoD 验证：只跑一次套件，并与已提交的 master 失败基线做机械差分（退出 0 = 通过）
bash scripts/test_baseline.sh --scope auto --base "$target_sha"
bash scripts/test_baseline.sh --scope auto --base "$target_sha" --plan
# 选测器、隔离 runner 或全局测试收集等基础设施改动
bash scripts/test_baseline.sh --scope fast --base "$target_sha"

# 查看 / 重建已提交的 master 基线（--update 仅限干净 master 工作区）
bash scripts/test_baseline.sh --show
bash scripts/test_baseline.sh --update

# 显式全量测试（不差分；每个 session 克隆独立临时测试库，避免并发互删 TEST_ 行）
bash scripts/test_isolated.sh fast

# 单域/单文件/单测试都走 isolated wrapper
bash scripts/test_isolated.sh api tests/api/test_auth_login.py::test_login_sets_cookie

# schema/migration 改动后刷新模板库，再跑验证
bash scripts/test_baseline.sh --scope auto --base "$target_sha" --refresh-template

# 测试前置依赖体检 / 安装（PostgreSQL 客户端、pytest、.env.test）
bash scripts/envsetup/install-test-deps.sh --check
sudo bash scripts/envsetup/install-test-deps.sh
```

> `scripts/test_isolated.sh` 是 agent 的**唯一测试入口**。如果 isolated 前置依赖不可用，先运行依赖检查/安装；不得降级到共享数据库或绕过 wrapper。`TTS_ERP_TEST_OFF=1` 对 agent 永久禁用。

> **一条 lane 只需一次套件运行。** master 的失败基线是 master 的属性，已提交在 `docs/testing/baseline-fast.txt`，不需要每条 lane 重新采集。旧的「采基线 + 合并后再跑 + 每个失败再复跑」流程要跑 2 轮以上全量套件，正是 worker 会撞上 30 分钟上限的主因。日常 DoD 通过 `--scope auto` 比较精确同步的目标 master SHA 所提交的基线。拟合并提交使用锁内确认的第一父提交。选测与隔离基础设施改动显式跑 `--scope fast`。**lane 不得用 `--update` 重建基线来让自己变绿。**

## 2. 数据导入

```bash
# 看 import plan（以脚本内 ALL_TABLES 为准，含 credentials + api_keys）
bash scripts/import_prod_to_test.sh --dry-run

# 实际把 prod 数据导入 tts_erp_v3_test（multi-pass FK）
bash scripts/import_prod_to_test.sh --yes

# 不拷 credentials（部分 FK 表会空）
bash scripts/import_prod_to_test.sh --exclude-credentials --yes
```

## 3. 服务管理

在 207 上查看系统级单元：

```bash
systemctl status tts-erp.service tts-erp-sync.service
journalctl -u tts-erp-sync.service -n 50 --no-pager
```

本地手动启动见 [测试指南](agent-testing.md#本地启动与手动验证)。生产服务变更由人工执行。

## 4. 发布与回退

按照 [部署手册](../ops/tts-erp.md#发布与回退) 选定已验收的完整 SHA。
API 与 Sync Worker 分别发布，涉及两个服务时传入相同的 `target_sha`。

```bash
bash scripts/prod_deploy.sh --verify-only
bash scripts/prod_sync_deploy.sh --verify-only
bash scripts/prod_deploy.sh "$target_sha"
bash scripts/prod_sync_deploy.sh "$target_sha"
bash scripts/prod_deploy.sh "$previous_api_sha"
bash scripts/prod_sync_deploy.sh --rollback "$previous_worker_sha"
```

两个入口都支持 `--dry-run`。依赖和数据库迁移安排见部署手册。

## 5. 浏览器渲染回归与端到端测试

```bash
# 浏览器渲染回归（无需服务；fast 默认就跑，单独跑用这条）
# isolated wrapper 内部选择 domain_browser and not slow
#  断言：13 页 @1440 横向溢出、ad-daily 多视口、computed 字体栈 ⊆ tokens 三套栈
bash scripts/test_isolated.sh browser

# 本地端到端冒烟（默认回环 :9877；用例在 tests/e2e/，fast 默认排除）
bash scripts/test_isolated.sh e2e
# 只读：/healthz、/endpoints、/v2/fx/latest、/v2/reporting/*；
# 基址/密钥可用 TTS_ERP_E2E_BASE、TTS_ERP_SERVICE_KEY 覆盖

# 7 步生产冒烟（healthz/auth/v2 读端点/page/sync_jobs）
bash prod-switch/postswitch-smoke.sh
```

## 6. 监控和日志

生产 API 业务日志在 207 的 `/opt/tts-erp/logs/{stdout,stderr}.log`。
Worker 日志使用 `journalctl -u tts-erp-sync.service`。
轮转配置和版本检查见 [部署手册](../ops/tts-erp.md#运行状态与日志)。

## 7. Schema 变更

```bash
# 流程：改 tts_erp_v2/db/models/ → 重新生成 → 应用；每个 migration 合入后都要重新生成快照
python3 scripts/regen_schema.py --db-url "postgresql://postgres@localhost/tts_erp_test_template"
# 只读 pg_dump，生成 docs/schema/schema_tts_erp.sql（IF NOT EXISTS 幂等兼容老库）；
# 生成源必须在当前 alembic head（用 psql 查 alembic_version 确认）；
# 新表/新列的含义同步写进 docs/schema/README.md 的数据结构索引

# 0045 妙手 package 数据归位：人工生产操作，一条命令完成
# guard + scoped backup + worker stop/start + migration + verification + immediate sync
ALLOW_PROD_DESTRUCTIVE=1 bash scripts/oneoff_migrate_0045_miaoshou_package_schema.sh --confirm

# 0046 妙手采购价清洗定时任务：先交互保存加密 browser session，再迁移并首跑
python3 scripts/configure_miaoshou_web_session.py \
  --account-id 12629145 --front-version 1790677442555 --confirm
ALLOW_PROD_DESTRUCTIVE=1 bash scripts/oneoff_migrate_0047_miaoshou_purchase_prices.sh --confirm
```

## 8. API key 管理

```bash
# key 管理 CLI
python3 api_keys.py create/list/revoke/rotate
# --prefix 定位；明文只创建时打印一次
```

## 9. 签名调试

```bash
# TikTok 签名调试
TTS_DEBUG_SIGN=1

# 妙手签名调试
MIAOSHOU_DEBUG_SIGN=1
# 在 stderr 打 canonical
```

## 10. 测试规范

- **TDD**：先写测试再实现
- **共享 fixtures**：在 `tests/conftest.py`（事务回滚隔离、`TEST_%` 哨兵数据）
- **测试环境隔离（2026-09-30）**：agent 默认跑 `bash scripts/test_isolated.sh ...`
  - 模板库：`tts_erp_test_template`；临时库：每次克隆一个 `tts_erp_test_*`，命令结束自动 drop
  - `bash scripts/test_isolated.sh --refresh-template fast` 会重建模板：prod schema 只读导入 → stamp prod alembic revision → upgrade 到当前 worktree head；如果 prod revision 不在当前 worktree，会警告并保留导入 schema
  - `bash scripts/test_isolated.sh` 是唯一标准入口；依赖缺失时先用 `install-test-deps.sh --check`/安装修复，不允许退回共享库
  - prod API service / `uvicorn` 本地启动仍读 `.env` 连 prod `tts_erp`，**零变更**
  - 安全护栏：tests/conftest.py 检测到 pytest 将指向 prod-shape dbname（`tts_erp` / `tts_erp_prod`）会 hard exit
  - `.env.test` 由 `bash scripts/envsetup/install-test-deps.sh` 生成；isolated wrapper 会验证其存在
  - 需要 prod-shaped 数据时只导入到测试库：`bash scripts/import_prod_to_test.sh --yes`
- **收尾标准**：跑不过 0 fail 不收尾
