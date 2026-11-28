# Agent 测试指南

本文定义 `tts-erp` 中编码 agent 唯一受支持的测试工作流。测试结果只有成功和失败两种，不需要测试报告或证据归档。

## 1. 环境准备

- 验证机器：开发机或现有 `tts-erp-test` 验证虚拟机（8 CPU / 7.2 GiB，固定容量，不扩容）。浏览器测试与前端构建并行时容易耗尽资源，优先最窄选择；有资源竞争迹象时单独重跑失败用例。
- 前置体检：`bash scripts/envsetup/install-test-deps.sh --check`；缺前置用对应安装脚本补齐。Bash 验证需要 Linux 或 WSL 环境。
- 新 worktree：`.venv`、`.env.test`、`frontend/node_modules` 均被 gitignore，必须先用 `bash scripts/envsetup/prepare-worktree.sh --scope auto --base "$target_sha"` 准备（`--scope backend` 免浏览器；`--scope fast` 完整验证；`--check`/`--dry-run` 不改环境）。
- Node：前端相关选择需要 `frontend/package.json` engine 范围内的 Node，以及 worktree 本地的 `npm ci --prefix frontend`。禁止跨 worktree symlink `frontend/node_modules`（ESM 解析按真实路径走，会解析到另一个 worktree 的 `package.json`），禁止在 lane 间共享可写 `node_modules` 或 Vite 缓存，不要手改 `node_modules`——用 `npm ci`。
- `.env` 规则：禁止编辑指向主仓的 `.env` symlink；禁止提交 `.env`、`.env.test`、凭证或拷贝的密钥；临时覆盖放进程环境或 gitignore 的 worktree 本地文件，用完删除。

## 2. 测试前准备

### 2.1 唯一入口与隔离

- 唯一入口是 `bash scripts/test_isolated.sh ...`：每次运行把 `tts_erp_test_template` 克隆为 per-session 临时 `tts_erp_test_*` 库，注入 `TTS_ERP_DB_URL_TEST`，跑完销毁。不存在直接运行器或共享数据库回退。
- 禁止对 `tts_erp`、`tts_erp_prod` 或任何生产形态库名运行测试；`tests/conftest.py` 在缺少当前隔离 run 证明时以状态 2 硬退出，仅"长得像测试库"的 URL 不构成授权。禁止设置 `TTS_ERP_TEST_OFF=1`。
- 禁止直接调用底层测试框架或低层包装器；禁止选择 `all`、`coverage` 或归档迁移域——`docs/archive/migrate-v1-to-v2-2026-08-29/` 下的套件包含或恢复生产迁移行为。
- 连接身份由 `scripts/_test_db_connections.py` 统一裁决：环境里的 `PG*` 路由/角色变量和未验证的 Docker 路由会被拒绝，不要静默 unset 或猜别的目的地。
- 多个隔离运行互不影响（各自克隆、各自销毁）；浏览器测试与前端构建受主机容量队列调度，排队等待属正常现象。

### 2.2 模板与迁移

- 迁移/表结构变化、模板缺失或过旧时，先刷新模板：`bash scripts/test_isolated.sh --refresh-template fast`。
- 克隆或前置失败时，停止并用 `bash scripts/envsetup/install-test-deps.sh --check` 修复，禁止换用其他运行器或共享数据库。
- 迁移验证只能走 isolated 运行器，对 `tts_erp_test_template` 及其临时克隆进行；迁移变更给 baseline 命令加 `--refresh-template`。

### 2.3 范围与命令

- 普通改动：跑最窄相关测试。`bash scripts/test_isolated.sh fast` / `unit` / `<domain>` / 具体文件或用例；domain 名可带或不带 `domain_` 前缀。
- 测试基础设施改动（选择、baseline 驱动、隔离、报告、全局收集与夹具）：`bash scripts/test_baseline.sh --scope fast --base "$target_sha"`。
- `tests/e2e/` 变更：在其获批环境中单独跑受影响的 smoke 命令，不进自动门禁。
- 文档类变更：验证链接、引用路径与 Markdown 即可，通常不需要应用测试。
- 浏览器测试：模块内复用 Chromium、每用例新 BrowserContext；罐头渲染等待 hydration 而不是 networkidle，通用布局/字体检查用 `tests/support/page_readiness.py`。

### 2.4 测试数据规则

- 测试数据加 `TEST_` 前缀；夹具只清理自己创建的行。
- 不假设生产形态的表为空；不删除或改动非测试行；不依赖执行顺序或其他测试遗留数据。

### 本地启动与手动验证

手动验收在开发机运行回环实例，使用本地测试配置和独立测试库；通过已生成的前端产物验证页面时先执行前端构建：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
npm ci --prefix frontend
PATH="$PWD/.venv/bin:$PATH" bash scripts/build_frontend.sh
```

在 Git 忽略的 `.env.local` 设置本地测试数据库 URL、测试用 Fernet key，以及页面需要的本地存储配置。启动前核对当前 shell 未继承生产数据库连接或其他生产环境变量。

```bash
.venv/bin/python -m uvicorn tts_erp_v2.app:app --env-file .env.local --host 127.0.0.1 --port 9877
# 从另一个终端检查本地实例
curl --fail --silent --show-error http://127.0.0.1:9877/healthz
```

自动化用例仍通过 `scripts/test_isolated.sh` 运行；手动验证的应用连接与隔离运行器每次创建的库属于不同生命周期。用完关闭本地进程。

## 3. 测试基准

- 正式门禁：`bash scripts/test_baseline.sh --scope auto --base "$target_sha"`，在集成锁下对已提交的 prospective merge **跑一次**；`target_sha` 是其第一父（精确同步的 master）。用 `--plan` 可先查看选择计划。
- 结果只有成功和失败：成功 = 相对目标基线零新增失败；失败 = 出现新增失败。失败时只对最小失败集重跑一次来定位，修好后再算完成；禁止反复全量重跑。
- master 基线是 master 的属性，提交在 `docs/testing/baseline-fast.txt`，lane 不重新测量。基线过期时只能从**干净 master worktree** 用 `bash scripts/test_baseline.sh --update` 重新生成；lane 禁止为了让自己通过而重生成基线。
- 查看基线：`bash scripts/test_baseline.sh --show`。分支未集成交付时，对其已提交 HEAD 跑对应 baseline，并说明集成待定。

## 4. 测试后清理

隔离运行器正常结束会自己销毁临时库，只有发现遗留 `tts_erp_test_*` 库时才需要手动清理：

1. 只删自己 lane 创建的库（库名含 branch/session/timestamp/pid）；禁止按前缀批量删除（`LIKE 'tts_erp_test_%'` 会误删其他 lane 正在使用的库）。
2. 批量清理前确认没有活动隔离运行：`pgrep -f "pytest -q"` 和 `pgrep -f test_baseline` 都为空。
3. 禁止删除模板族：`tts_erp_test_template` 及所有 `*_template_*`（`template_ads_ack`、`template_price_data` 等是其他 lane 的基线）。
4. 逐条执行 `DROP DATABASE`，不要用 `psql -f`（整个脚本包在单事务里，`DROP DATABASE` 在事务内被拒，整批静默失败）。

手动验证的本地进程用完关闭。

## 5. 常见问题

- **conftest 同名冲突**：`tests/` 无 `__init__.py`，pytest 按裸名导入所有 `conftest.py`；症状是单文件通过、宽选择失败，报 `AttributeError: module 'conftest' has no attribute '_mock_payload'`。按包路径导入：`from tests.browser import conftest as browser_conftest`。
- **模块双实例导致 patch 静默失效**：同一文件被同时导入为 `conftest` 和 `tests.browser.conftest` 两个对象，patch 打在一个上、服务器读另一个；页面渲染的是未打 patch 的罐头数据，没有异常，看起来像渲染器回归。动选择器前先打印两个 `sys.modules` 身份；新增罐头端点保持 `_canned_payload()` 间接层。
- **页面渲染者变更（Jinja → React）**：旧 DOM 断言只会 `wait_for_selector` 超时，不报错。切换前 `git grep -n "<old-element-id>" -- tests/` 找出依赖旧标记的测试；优先用 `tests/support/page_readiness.py` 的就绪契约，不要硬编码选择器。
- **删 import 后名字残留**：`grep` 仍找得到名字、模块仍能导入，只有真正调用时才炸。删 import/常量/类后跑 `python scripts/check_undefined_names.py tts_erp_v2`（生产树必须为零）；测试树的既有记录见 `docs/testing/undefined-names-baseline.txt`，名字移动或新增即红。
- **`--scope auto` 空计划返回 0 但不跑测试**：文档类变更或 `head == base` 时会打印 `no application tests selected` 并退出 0。要看 master 真实状态用 `--scope fast`；信任 auto 结论前先看 `--plan`。
