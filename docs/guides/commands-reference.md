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

在本机仓库根目录执行以下命令。首次运行会安装缺少的前端依赖，默认构建前端后在前台启动服务，后端读取 `.env.local`。

```bash
bash scripts/local_deploy.sh                  # 构建前端并启动 127.0.0.1:9877
bash scripts/local_deploy.sh --frontend-only  # 服务已运行时更新前端，完成后刷新页面
```

Windows PowerShell 可用 Git Bash 执行 `& 'C:/Program Files/Git/bin/bash.exe' scripts/local_deploy.sh`。
需要重启本机后端时，先在原终端按 Ctrl+C，再运行默认命令。前端锁文件变更后先执行 `npm ci --prefix frontend`。

服务器进程由 systemd 管理：

```bash
# 重启 API = systemctl --user restart tts-erp.service
bash restart.sh

# 改了 jobs/ 或 sync_worker/ 后必须单独跑
systemctl --user restart tts-erp-sync.service
```

## 4. 部署（一键脚本）

```bash
# HP desk 测试环境：构建前端 → release worktree checkout → 重启 API
# （主 worktree HEAD 一致时连带重启 sync/publish worker）→ 本机校验
bash scripts/test_deploy.sh            # 部署当前 HEAD
bash scripts/test_deploy.sh <ref>      # 部署指定分支/tag/commit
bash scripts/test_deploy.sh --dry-run  # 只看计划不改动

# 207 生产环境（207.57.126.199）：构建 → tar 管道同步（tts_erp_v2 原子替换、
# 留 tts_erp_v2.prev 回滚副本）→ 重启系统服务 → 207 本机 + 公网校验
bash scripts/prod_deploy.sh                  # 部署 origin/master（先 fetch），需逐字输入 207.57.126.199 确认
bash scripts/prod_deploy.sh --yes <ref>      # 跳过交互确认（脚本/CI）
bash scripts/prod_deploy.sh --dry-run        # 只看计划不改动
bash scripts/prod_deploy.sh --verify-only    # 只读校验当前线上状态

# 依赖清单（pyproject.toml 等）有变化时默认只警告，确认后加 --sync-deps 安装
```

> 两个脚本都只部署**已提交**的 commit（在临时 worktree 里构建前端），永不执行数据库迁移
> （检测到新 alembic 迁移只警告，迁移一律人工），不覆盖目标机的 `.env` / `.venv` / `logs`。
> 目标目录/主机可用环境变量覆盖，详见脚本头部注释。

## 5. 浏览器渲染回归与端到端测试

```bash
# 浏览器渲染回归（无需服务；fast 默认就跑，单独跑用这条）
# isolated wrapper 内部选择 domain_browser and not slow
#  断言：13 页 @1440 横向溢出、ad-daily 多视口、computed 字体栈 ⊆ tokens 三套栈
bash scripts/test_isolated.sh browser

# live 端到端冒烟（需 :9877 在跑；用例在 tests/e2e/，默认被 fast 排除）
bash scripts/test_isolated.sh e2e
# 只读：/healthz、/endpoints、/v2/fx/latest、/v2/reporting/*；
# 基址/密钥可用 TTS_ERP_E2E_BASE、TTS_ERP_SERVICE_KEY 覆盖

# 7 步生产冒烟（healthz/auth/v2 读端点/page/sync_jobs）
bash prod-switch/postswitch-smoke.sh
```

## 6. 监控和日志

```bash
# 进程状态
systemctl --user status tts-erp{,-sync}.service

# systemd 日志
journalctl --user -u tts-erp -n 50

# 业务日志：logs/stderr.log 抓 traceback
# watchdog 巡检发现看 logs/watchdog.log（约定 agent 定时扫，代告警）
```

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
