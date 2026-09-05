# tts-erp 安装 / 部署

> ⚠️ **2026-09-03 重写**：v2 自 2026-08-29 切流后整套服务架构变了（FastAPI/uvicorn `:9877` + APScheduler sync-worker + 10 schema PG）。
> 旧版基于 `tts_erp.py` 单文件服务的文档已废弃。本文档只保留 v2 启动 / 部署 / 故障排查；端点契约和数据模型请看下列指针。

## 单一真理源

| 文档 | 内容 |
| --- | --- |
| [`../README.md`](../README.md) | v2 架构 / 同步 / 鉴权 / 调试 / 已知问题 |
| [`../AGENTS.md`](../AGENTS.md) | AI agent 操作指南（HMAC 签名、凭证单源、DO/DON'T） |
| [`../tech-doc/external-api.md`](../tech-doc/external-api.md) | v2 端点契约（活契约；任何外部对接以此为准） |
| [`../tech-doc/data-model-target-v3.md`](../tech-doc/data-model-target-v3.md) | 10 schema / 60 表 V3 真理源 |
| [`../tech-doc/api-key-auth-design.md`](../tech-doc/api-key-auth-design.md) | API key 鉴权设计 |
| [`../tech-doc/browser-login-design.md`](../tech-doc/browser-login-design.md) | 浏览器 cookie 会话设计 |
| [`../tech-doc/refactor-tech-plan-v2.md`](../tech-doc/refactor-tech-plan-v2.md) | v2 重构技术方案（已实施） |
| [`../tech-doc/test-domains.md`](../tech-doc/test-domains.md) | 测试按域切片 |
| [`./analytics-sync.md`](./analytics-sync.md) | Chrome 扩展广告分析 ingest 协议 |

## v2 进程托管

v2 由 systemd user 单元托管（`~/.config/systemd/user/`，`Linger=yes`，开机自启）：

- `tts-erp.service` — `tts_erp_v2/app:app`（FastAPI/uvicorn，端口 9877）
- `tts-erp-sync.service` — `tts_erp_v2.sync_worker.main`（APScheduler 同步）
- `oauth-receiver.service` — **仅 4 周回滚观察期保留**（自 v2 切流 2026-08-29 起）
- `tts-erp-watchdog.timer` — 同步巡检（每 10min 跑 `scripts/watchdog_sync.py`）

```bash
systemctl --user status tts-erp.service        # v2 API
systemctl --user status tts-erp-sync.service   # sync-worker
systemctl --user status oauth-receiver.service # 4 周观察期内保留
systemctl --user restart tts-erp.service       # 等价 restart.sh
systemctl --user restart tts-erp-sync.service  # 改了 jobs/ 或 sync_worker/ 后必须单独跑

journalctl --user -u tts-erp -n 50             # systemd 日志
journalctl --user -u tts-erp-sync -n 50
```

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
- 公网（已配 NAT 穿透）：`http://daqiang.nat100.top`（**已 strip 9877 端口**）
- 对客户端 / 文档 / TikTok redirect URL 一律用 `daqiang.nat100.top/<path>`，不带端口
- 本机 curl 测试仍可 `http://127.0.0.1:9877/...`（带端口）

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
