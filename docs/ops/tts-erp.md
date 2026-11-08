# tts-erp 安装 / 部署

> ⚠️ **2026-09-03 重写**：v2 自 2026-08-29 切流后整套服务架构变了（FastAPI/uvicorn `:9877` + APScheduler sync-worker + 10 schema PG）。
> 旧版基于 `tts_erp.py` 单文件服务的文档已废弃。本文档只保留 v2 启动 / 部署 / 故障排查；端点契约和数据模型请看下列指针。

## 单一真理源

| 文档 | 内容 |
| --- | --- |
| [`../README.md`](../README.md) | v2 架构 / 同步 / 鉴权 / 调试 / 已知问题 |
| [`../AGENTS.md`](../../AGENTS.md) | AI agent 操作指南（HMAC 签名、凭证单源、DO/DON'T） |
| [`../docs/api/external-api.md`](../api/external-api.md) | v2 端点契约（活契约；任何外部对接以此为准） |
| [`../docs/architecture/data-model-target-v3.md`](../architecture/data-model-target-v3.md) | 10 schema / 37 表 + 2 view V3 真理源（2026-09-05 按 ADR-0003 §2.6 同步） |
| [`../docs/design/api-key-auth-design.md`](../design/api-key-auth-design.md) | API key 鉴权设计 |
| [`../docs/archive/browser-login-design.md`](../archive/browser-login-design.md) | 浏览器 cookie 会话设计 |
| [`../docs/archive/refactor-tech-plan-v2.md`](../archive/refactor-tech-plan-v2.md) | v2 重构技术方案（已实施） |
| [`../docs/guides/test-domains.md`](../guides/test-domains.md) | 测试按域切片 |
| [`./analytics-sync.md`](./analytics-sync.md) | Chrome 扩展广告分析 ingest 协议 |

## 本机虚拟机测试服务器（项目记忆）

- 适用范围：tts-erp 的测试服务器为 **HP desk `schan@192.168.1.2`**（主机名 `schan-HP-ProDesk-400-G2-MINI`，Ubuntu 24.04 LTS）。代码 checkout 在 `/home/schan/tts-erp`。
- 连接方式：SSH 用户 `schan`；本机 `~/.ssh/config` 已配置别名 `hp-desk` / `hpdesk`（HostName 192.168.1.2，IdentityFile `~/.ssh/id_ed25519`），可直接 `ssh hp-desk`。
- 服务器上已就绪：项目 venv `/home/schan/tts-erp/.venv`（Python 3.13）、docker `postgres:18`（`tts_erp` 库）与 docker `minio`、`tts_erp_test_template` 测试模板库、`.env.test` 测试连接串。
- 使用方式：数据库测试只允许从仓库根目录通过 `bash scripts/test_isolated.sh ...` 使用测试模板和临时克隆库，遵守 `AGENTS.md` 的数据库与生产安全边界。前置依赖体检：`bash scripts/envsetup/install-test-deps.sh --check`。
- 验证证据：2026-11-08 从 Windows 本机以批处理模式 SSH 登录成功；`hostname` 为 `schan-HP-ProDesk-400-G2-MINI`，系统为 Ubuntu 24.04.5 LTS；`install-test-deps.sh --check` 四项全部 `[OK]`，`tts_erp_test_template` 存在。
- 失效条件：机器重装、IP、SSH 用户或密钥发生变化，或用户另行指定测试服务器时，须重新验证登录并更新本条记录；登录失败时不得继续引用本次成功结果作为当前可用性证明。

```powershell
ssh hp-desk
```

### 模板库刷新依赖生产 schema 源（2026-11-08 实测）

`scripts/test_isolated.sh --refresh-template` 需要一个**生产形状库**的只读连接串来取当前 alembic 版本和 schema。查找顺序（`scripts/test_isolated.sh` 的 `source_prod_url()`）：

1. 环境变量 `TTS_ERP_DB_URL_PROD_SOURCE`
2. 仓库根目录 `.env` 的 `TTS_ERP_DB_URL`
3. 上级目录 `../../.env` 的 `TTS_ERP_DB_URL`

**HP desk 当前没有 `.env`**，所以刷新路径不可自助执行。

**但当前模板库不陈旧**，无需刷新。2026-11-08 实测比对：

| 库 | alembic 版本 | 表数 |
| --- | --- | --- |
| `tts_erp`（生产形状） | `0076_media_packs` | 306 |
| `tts_erp_test_template` | `0076_media_packs` | 306 |

两者一致，且与仓库最新 migration（`alembic/versions/0076_media_packs.py`）三方对齐。

**注意**：`.env.test` 里的 `TTS_ERP_DB_URL` 和 `TTS_ERP_DB_URL_TEST` 都指向 `tts_erp_v3_test`，**不是生产形状库**，不能用来充当刷新源。

**何时需要处理**：当仓库新增 migration（`alembic/versions/`）而模板库版本落后时，必须先解决生产 schema 源问题，否则 `--refresh-template` 必然失败。届时需要运维提供一条**只读**的生产形状库连接串，写入 HP desk 的 `.env`。

### 残留临时库清理

崩溃或中断的测试会留下 `tts_erp_test_<branch>_<session>_<时间戳>_<pid>` 形态的临时库（正常流程跑完会自动 drop）。2026-11-08 清点：22 个非 template 残留库 + 9 个 `*_template_*` 基线库，合计约 432 MB。

- **可删**：非 template 残留库。删除前确认 `pg_stat_activity` 中无连接，且无正在运行的 `test_isolated.sh` 进程。
- **不可删**：`tts_erp_test_template` 及所有 `*_template_*` 库（`template_ads_ack`、`template_price_data`、`template_price_e2e`、`template_spu_compare_0069`、`video_publish_v1x/v2x_template`、`monitor_health_template` 等），它们是其他 lane 的测试基线。

## v2 进程托管

v2 由 systemd user 单元托管（`~/.config/systemd/user/`，`Linger=yes`，开机自启）：

- `tts-erp.service` — `tts_erp_v2/app:app`（FastAPI/uvicorn，端口 9877）
- `tts-erp-sync.service` — `tts_erp_v2.sync_worker.main`（APScheduler 同步）
- `tts-erp-watchdog.timer` — 同步巡检（每 10min 跑 `scripts/watchdog_sync.py`）

```bash
systemctl --user status tts-erp.service        # v2 API
systemctl --user status tts-erp-sync.service   # sync-worker
systemctl --user restart tts-erp.service       # 等价 restart.sh
systemctl --user restart tts-erp-sync.service  # 改了 jobs/ 或 sync_worker/ 后必须单独跑

journalctl --user -u tts-erp -n 50             # systemd 日志
journalctl --user -u tts-erp-sync -n 50
```

## 一键安装（scripts/envsetup/install.sh）

`scripts/envsetup/install.sh` 把下面「一键启动 / 部署」的手工步骤固化成一个幂等脚本，可反复执行：

```bash
# 预览将要执行的命令（不改动系统）
bash scripts/envsetup/install.sh --dry-run

# 交互式安装（生产库迁移会逐次确认）
bash scripts/envsetup/install.sh

# 免交互 + 显式执行数据库迁移（生产库迁移必须显式 --migrate）
bash scripts/envsetup/install.sh -y --migrate
```

10 个步骤：preflight（`.env` 存在 / 0600 / 必需变量 / Python ≥ 3.13 / `systemd --user`）→
`logs/` `tests/` → `.venv` + `pip install -e .` → `restart.sh` 可执行 → 安装 systemd user 单元
（`tts-erp.service` / `tts-erp-sync.service` / `tts-erp-watchdog.timer` / `tts-erp-pgbackup.timer`，
已存在的单元默认保留，`--force-units` 才覆盖并备份）→ PG `pg_isready` → `alembic upgrade head`
→ `enable` + `start/restart` 服务 → 轮询 `/healthz` 直到返回 `service:"tts-erp-v2"` → 运维摘要。

常用选项：`--skip-deps` / `--skip-migrate` / `--migrate` / `--skip-units` / `--skip-services` /
`--force-units` / `--dry-run` / `-h`。

安全约束（AGENTS.md §3）：

- 生产形态库（`tts_erp` / `tts_erp_prod` / `tts_erp_prod_*`）上的 `alembic upgrade head`
  属人工操作：非交互模式必须显式 `--migrate`；执行时只对本次 alembic 子进程设置
  `ALLOW_PROD_DESTRUCTIVE=1`（绕过 `alembic/env.py` 的 prod-shape guard）。
- 脚本不创建、不改写任何密钥，`.env` 只校验存在性和 0600 权限；不触碰测试库，
  不执行 `DELETE` / `TRUNCATE` / `DROP`。
- 健康检查失败（30s 无响应或返回非 v2）时脚本以非 0 退出，并打印
  `journalctl --user -u tts-erp -n 50` 与 `logs/stderr.log` 的排查入口。

## 一键启动 / 部署

```bash
# 一次性（已部署则跳过）
ssh schan@192.168.47.130 "mkdir -p /home/schan/tts-erp/{logs,setup,tests}"

# 部署
scp F:\path\to\tts-erp\* schan@192.168.47.130:/home/schan/tts-erp/
ssh schan@192.168.47.130 "chmod 600 /home/schan/tts-erp/.env && chmod +x /home/schan/tts-erp/restart.sh"

# PG schema（幂等；v2 用 alembic，旧 schema_*.sql 是应急回滚用）
.venv/bin/alembic upgrade head

# 启动
ssh schan@192.168.47.130 "bash /home/schan/tts-erp/restart.sh"
```

## 端口 + 公网

- 内网：`http://127.0.0.1:9877`
- 公网（已配 HTTPS + NAT 穿透）：`https://chen.daqiangplatform.com/tts`（**已 strip 9877 端口**，保留 `/tts` 前缀）
- 对客户端 / 文档 / TikTok redirect URL 一律用 `https://chen.daqiangplatform.com/tts/<path>`，不带端口
- 本机 curl 测试仍可 `http://127.0.0.1:9877/...`（带端口）

### 生产 MinIO 预签名

207 上 `/opt/tts-erp/.env` 的 `MINIO_ENDPOINT=127.0.0.1:6005` 用于应用经
frp 访问 MinIO。浏览器经 `MINIO_PUBLIC_HOST` 的 `/minio` 路径访问时，
HP desk 的 nginx 向 MinIO 转发 `Host: 127.0.0.1:9000`。在 207 的环境文件中
设置 `MINIO_PRESIGN_ENDPOINT=127.0.0.1:9000`，让上传和预览 URL 的签名匹配
该请求头。部署重启后，从资产管理的视频卡片打开素材，确认能播放和拖动进度。
直连地址与代理转发 Host 相同的环境可省略 `MINIO_PRESIGN_ENDPOINT`。

## 健康检查

```bash
curl -s http://127.0.0.1:9877/healthz
# {"status":"ok","service":"tts-erp-v2","auth_mode":"enforce"}
```

`service:"tts-erp-v2"` 是判定 v2 真在跑的唯一信号；v1 旧 `tts_erp.py` 只会返 `{"status":"ok"}`。

## 故障排查速查

| 症状 | 原因 | 修复 |
| --- | --- | --- |
| `106001 invalid sign` | HMAC 签名格式错 | `TTS_DEBUG_SIGN=1` 起服务看 canonical，对比 AGENTS.md §2.2 |
| `105005 Access denied` | app 没勾对应 scope | TikTok Partner Center 改 app scope + 重新授权 |
| `36009004 PageSize is required` | body 字段名/格式错 | 查 TikTok API 文档的 Request Body |
| v2 端点 `?shop_id=` 没过滤 | v2 只认内部 id | 先 `GET /v2/commerce/channel-accounts` 查 `shop_pk` |
| `TTS_ERP_DB_URL not configured` | `.env` 缺 DB URL | 配 .env |
| `psycopg.OperationalError` | PG 容器 down | `docker exec postgres pg_isready` |
| 物流数据多日不更新 | `tts-erp-sync.service` 未 active / tracking 排序错 | `systemctl --user status tts-erp-sync.service` + 看 `integration.sync_jobs` |

## 相关历史

- `handoff.md`（仓库根）— 跨 session 交接笔记（v1 → v2 切流过程）
- `CHANGELOG.md`（仓库根）— 按日期的变更日志
- `AGENTS.md`（仓库根）§6 文件清单 — 当前完整目录结构与状态
