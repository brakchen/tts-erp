# tts-erp 进程托管 / 目录地图

> 本文档包含 tts-erp 项目的进程托管和目录结构信息。
> 通用约束和边界规则见 `AGENTS.md`。

## 1. systemd user units

`Linger=yes` 开机自启，无需登录：

- **`tts-erp.service`**：uvicorn API，cwd=仓库根，`EnvironmentFile=.env`
- **`tts-erp-sync.service`**：APScheduler worker，安装脚本 `prod-switch/install-sync-worker.sh`
- **`tts-erp-publish.service`**：单设备 TikTok 发布 Worker；user unit 为 `scripts/systemd/tts-erp-publish.service`，只经受控 ADB/Artemis adapter 工作
- **`tts-erp-watchdog.timer`**：每 10min 巡检 → `logs/watchdog.log`
- **`tts-erp-logrotate.timer`**：每 6h 检查 `logs/{stdout,stderr,watchdog}.log`，
  > 20MB 则滚动（`scripts/logrotate/tts-erp.conf`，copytruncate 适配 systemd
  `append:` 长开 FD，无需 root；`scripts/systemd/tts-erp-logrotate.*`）。`sync_worker.log`
  由 RotatingFileHandler 自转，不在此列

## 2. 目录结构

```text
tts_erp_v2/
├── app.py               # FastAPI build_app() 工厂（中间件顺序见 §6）
├── api/v2/              # 路由：commerce / reporting / pages / spu_images / auth /
│                        #   llm_context / admin（rate-limit / purge-plugin-data / shops 注册：插件店铺人工登记进
│                        #   commerce.shops，data_source='plugin'（枚举 api|plugin），仅服务查询关联，readwrite 角色） /
│                        #   analytics（插件广告 dump ingest：/v2/analytics/sync/* → 落 plugin.* schema）
├── middleware/          # auth.py（角色矩阵）、session_auth.py、rate_limit.py、access_log.py
├── proxy/               # 出站层：tts_shop/（TikTok 签名+客户端）、miaoshou/、token_service.py
├── jobs/                # 同步 job 实现：tiktok/*、miaoshou/*、
│                        #   reporting（cost_snapshots 6h / profit_daily 1h）、token_refresh（6h）、runner
├── sync_worker/         # APScheduler；JOBS 注册表 + 调度状态（顶部 NOTE，以它为准）；
│                        #   operator controls use readwrite-gated /v2/pages/sync-jobs;
│                        #   manual trigger is readwrite+, enable/disable is admin
├── db/models/           # 13 schema SQLAlchemy 模型 — publishing.py 为视频发布任务/attempt/Worker 心跳；
│                        #   miaoshou.py 为妙手 source-owned 包裹/采购价域 8 张表；
│                        #   plugin.py 为插件 dump 的结构化表：订单/物流/结算 7 张
│                        #   （orders/order_lines/shipments/tracking_events/settlements/
│                        #   settlement_details/raw_log，原 chrome_sync.py）+ 广告 5 张（ad_today/ad_daily/
│                        #   ad_raw_log/plugin_logs；monthly dump 仅写 raw log）
├── plugin/orders/       # 插件 dump 数据访问层：解析 TikTok 响应 + upsert 到 plugin.*
│                        #   （订单/物流/结算；原 tts_erp_v2/chrome_sync/）
├── plugin/ads/          # 插件广告 dump 数据访问层：coverage 查询 / upsert ad_* / plugin_logs
│                        #   （原 tts_erp_v2/analytics/{domain,repository}.py）
├── analytics/ reporting/ storage/
│                        # analytics/ 只留读侧（spu_roi.py ROI 看板，读 plugin.ad_*）
├── publishing/          # 视频发布状态机、owner-fenced repository、dispatcher、受控 adapters 与 Worker
└── static/

miaoshou/                # 妙手 SDK 包（独立包：client + miaoshou_signing.py；无 HTTP 路由，进程内用）
api_keys.py              # key 管理 CLI     docs/schema/schema_tts_erp.sql   restart.sh
tests/                   # v2 测试（api/jobs_*/middleware/proxy/reporting/storage/sync_worker）
docs/                       # 全部专题文档（目录说明见 docs/README.md；端点活契约 docs/api/external-api.md）
│   └── ops/                # 部署/运维文档（tts-erp.md / analytics-sync.md / pg-backup-design.md）

根目录：conftest.py（pytest 路径引导）· .env（0600，勿 commit）· docs/archive/handoff.md（旧交接记录）·
CHANGELOG.md · pyproject.toml（ruff/pytest 配置）
```

## 3. 关键文件说明

### 3.1 app.py

FastAPI `build_app()` 工厂，中间件顺序：

- RateLimit 最内
- Auth
- CORS
- AccessLog 最外
- Auth 必须在 RateLimit 之前才能按 key 分桶

### 3.2 sync_worker/scheduler.py

APScheduler 调度器，JOBS 注册表在文件顶部 `NOTE`，以它为准，勿重复维护。

### 3.3 db/models/

13 schema SQLAlchemy 模型：

- `publishing.py`：`publishing.video_publish_tasks / video_publish_attempts / worker_heartbeats`，分别保存任务与 cleanup owner 状态、不可变 attempt 身份和 Worker readiness；
- `miaoshou.py`：妙手 source-owned 包裹/采购价域 8 张表（package raw/header/item/gift、purchase raw/candidate、cursor、issue）；
- `plugin.py`：插件 dump 的订单、物流、结算和广告表；
- 订单/物流/结算 7 张：orders、order_lines、shipments、tracking_events、settlements、settlement_details、raw_log
- 广告：ad_today、ad_daily、ad_raw_log、plugin_logs；monthly dump 仅写 raw log

### 3.4 proxy/tts_shop/

TikTok 签名+客户端，内部处理 HMAC 签名 / `x-tts-access-token` / shop_cipher 位置 / 翻页 / 过期 token 续期。
