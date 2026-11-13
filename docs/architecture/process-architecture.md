# tts-erp 进程托管 / 目录地图

> 本文档包含 tts-erp 项目的进程托管和目录结构信息。
> 通用约束和边界规则见 `AGENTS.md`。

## 1. 进程托管

- **生产 API `tts-erp.service`** 在生产机（`85.189.59.112`）使用系统级 systemd，从 `/opt/tts-erp` 运行。
- **生产 Sync Worker `tts-erp-sync.service`** 在生产机使用独立系统级单元，从
  `/opt/tts-erp-sync/releases/<sha>` 运行 APScheduler。它与 API 共享数据存储，分别发布和回退。
- **HP desk** 运行生产 PostgreSQL、MinIO、frpc 和备份定时器。旧 API 与视频发布 Worker（`tts-erp-publish.service`）已停止。
  旧 Sync Worker 保留代码、配置和单元，供回退时手动启动。
- **RunningHub Worker** 由 `tts_erp_v2.runninghub.worker` 提供，按实际部署独立安装。
  它复用 PostgreSQL 和 MinIO，按凭据限制在途任务，优先恢复已受理的任务。见 [RunningHub](../api/runninghub.md)。

日志、轮转、开机自启与服务检查见 [部署手册](../ops/tts-erp.md#运行状态与日志)。

## 2. 目录结构

```text
tts_erp_v2/
├── app.py               # FastAPI build_app() 工厂（中间件顺序见 §3.1）
├── api/v2/              # 路由：commerce / reporting / pages / spu_images / auth /
│                        #   llm_context / admin（rate-limit / purge-plugin-data / shops 注册：插件店铺人工登记进
│                        #   commerce.shops，data_source='plugin'（枚举 api|plugin），仅服务查询关联，readwrite 角色） /
│                        #   analytics（插件广告 dump ingest：/v2/analytics/sync/* → 落 plugin.* schema）
├── middleware/          # auth.py、session_auth.py、rate_limit.py、access_log.py
├── proxy/               # 出站层：tts_shop/（TikTok 签名+客户端）、miaoshou/、token_service.py
├── jobs/                # 同步 job 实现：tiktok/*、miaoshou/*、
│                        #   reporting（cost_snapshots 6h / profit_daily 1h）、token_refresh（6h）、runner
├── sync_worker/         # APScheduler；JOBS 注册表 + 调度状态（顶部 NOTE，以它为准）；
│                        #   operator controls use readwrite-gated /v2/pages/sync-jobs;
│                        #   manual trigger is readwrite+, enable/disable is admin
├── db/models/           # 16 schema SQLAlchemy 模型 — publishing.py 为视频发布任务/attempt/Worker 心跳；
│                        #   runninghub.py 为换装批次/Attempt、工作流/素材快照、配额、上传缓存与成片；
│                        #   media.py 为全局媒体资产库、素材包和数字人模型；
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
- Auth（角色策略在 `access/_policy.py`）
- DocsAuth
- CORS
- AccessLog 最外
- Auth 必须在 RateLimit 之前才能按 key 分桶

### 3.2 sync_worker/scheduler.py

APScheduler 调度器，JOBS 注册表在文件顶部 `NOTE`，以它为准，勿重复维护。
任务事务、联网与失败边界见 [同步事务约束](sync-transaction-boundaries.md)。

### 3.3 db/models/

16 schema SQLAlchemy 模型：

- `publishing.py`：`publishing.video_publish_tasks / video_publish_attempts / worker_heartbeats`，分别保存任务与 publish/cleanup owner 状态（每轮 execution generation、下载前 generation-scoped `spool_path`、上传 generation/key/PUT expiry 独立）、含 profile/verification level 的完整不可变 attempt submission 身份和 Worker readiness；spool cleanup 精确处理退休 final/`.part`，对象 cleanup 固定退休 generation 并受 expiry+grace DB-time gate，设备 cleanup 只删除退休 generation 的精确 filesystem/MediaStore 路径；
- `media.py`：全局媒体资产库、素材包和数字人。素材包与档案通过关联表复用资产，浏览器凭预签名 URL 直传对象存储。见 [媒体资产方案](../design/media-asset-library.md)；
- `miaoshou.py`：妙手 source-owned 包裹/采购价域 8 张表（package raw/header/item/gift、purchase raw/candidate、cursor、issue）；
- `plugin.py`：插件 dump 的订单、物流、结算和广告表；
- 订单/物流/结算 7 张：orders、order_lines、shipments、tracking_events、settlements、settlement_details、raw_log
- 广告：ad_today、ad_daily、ad_raw_log、plugin_logs；monthly dump 仅写 raw log

### 3.4 proxy/tts_shop/

TikTok 签名+客户端，内部处理 HMAC 签名 / `x-tts-access-token` / shop_cipher 位置 / 翻页 / 过期 token 续期。
