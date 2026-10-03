# tts-erp 命令参考

> 本文档包含 tts-erp 项目的常用命令和测试规范。
> 通用约束和边界规则见 `AGENTS.md`。

## 1. 测试命令

```bash
# 日常全量测试（默认入口；每个 session 克隆独立临时测试库，避免并发互删 TEST_ 行）
bash scripts/test_isolated.sh fast

# 单域/单文件/单测试仍走 isolated wrapper，参数透传给 scripts/test.sh
bash scripts/test_isolated.sh api tests/api/test_auth_login.py::test_login_sets_cookie

# schema/migration 改动后刷新模板库，再跑验证
bash scripts/test_isolated.sh --refresh-template fast

# 测试前置依赖体检 / 安装（PostgreSQL 客户端、pytest、.env.test）
bash scripts/envsetup/install-test-deps.sh --check
sudo bash scripts/envsetup/install-test-deps.sh
```

> `scripts/test_isolated.sh` 是**唯一标准测试入口**。
>
> 直接调 `scripts/test.sh` 的 shared-DB 回退路径**已弃用**：它不克隆临时库、
> 直接写常驻 `tts_erp_v3_test`，并发时互删 `TEST_` 行。`scripts/test.sh` 仅保留为
> `test_isolated.sh` 内部委托的 pytest 包装层。
> 确实无法走 isolated（例如 `createdb` 装不上）时才允许兜底，必须串行并记录原因：
>
> ```bash
> flock -n /tmp/tts-erp-test.lock bash scripts/test.sh fast
> ```
>
> 跳过 `.env.test` source（`TTS_ERP_TEST_OFF=1`）仅限人工迁移/手动调试，agent 禁用。

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

```bash
# 重启 API = systemctl --user restart tts-erp.service
bash restart.sh

# 改了 jobs/ 或 sync_worker/ 后必须单独跑
systemctl --user restart tts-erp-sync.service
```

## 4. 端到端测试

```bash
# live 端到端冒烟（需 :9877 在跑；用例在 tests/e2e/，默认被 fast 排除）
bash scripts/test_isolated.sh e2e        # = pytest -m "domain_e2e and not slow" tests/
# 只读：/healthz、/endpoints、/v2/fx/latest、/v2/reporting/*；
# 基址/密钥可用 TTS_ERP_E2E_BASE、TTS_ERP_SERVICE_KEY 覆盖

# 7 步生产冒烟（healthz/auth/v2 读端点/page/sync_jobs）
bash prod-switch/postswitch-smoke.sh
```

## 5. 监控和日志

```bash
# 进程状态
systemctl --user status tts-erp{,-sync}.service

# systemd 日志
journalctl --user -u tts-erp -n 50

# 业务日志：logs/stderr.log 抓 traceback
# watchdog 巡检发现看 logs/watchdog.log（约定 agent 定时扫，代告警）
```

## 6. Schema 变更

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

## 7. API key 管理

```bash
# key 管理 CLI
python3 api_keys.py create/list/revoke/rotate
# --prefix 定位；明文只创建时打印一次
```

## 8. 签名调试

```bash
# TikTok 签名调试
TTS_DEBUG_SIGN=1

# 妙手签名调试
MIAOSHOU_DEBUG_SIGN=1
# 在 stderr 打 canonical
```

## 9. 测试规范

- **TDD**：先写测试再实现
- **共享 fixtures**：在 `tests/conftest.py`（事务回滚隔离、`TEST_%` 哨兵数据）
- **测试环境隔离（2026-09-30）**：agent 默认跑 `bash scripts/test_isolated.sh ...`
  - 模板库：`tts_erp_test_template`；临时库：每次克隆一个 `tts_erp_test_*`，命令结束自动 drop
  - `bash scripts/test_isolated.sh --refresh-template fast` 会重建模板：prod schema 只读导入 → stamp prod alembic revision → upgrade 到当前 worktree head；如果 prod revision 不在当前 worktree，会警告并保留导入 schema
  - `bash scripts/test_isolated.sh` 是唯一标准入口；直接调 `scripts/test.sh` 的 shared-DB 回退路径已弃用（不克隆临时库、直写常驻 `tts_erp_v3_test`，并发互删 `TEST_` 行），确实需要时必须 `flock -n /tmp/tts-erp-test.lock ...` 并记录原因
  - prod API service / `uvicorn` 本地启动仍读 `.env` 连 prod `tts_erp`，**零变更**
  - 安全护栏：tests/conftest.py 检测到 pytest 将指向 prod-shape dbname（`tts_erp` / `tts_erp_prod`）会 hard exit
  - scripts/test.sh 会在 .env.test 缺失时直接退出；`.env.test` 由 `bash scripts/envsetup/install-test-deps.sh` 生成
  - 需要 prod-shaped 数据时只导入到测试库：`bash scripts/import_prod_to_test.sh --yes`
- **收尾标准**：跑不过 0 fail 不收尾
