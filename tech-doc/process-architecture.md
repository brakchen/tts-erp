# tts-erp 进程托管 / 目录地图

> 本文档包含 tts-erp 项目的进程托管和目录结构信息。
> 通用约束和边界规则见 `AGENTS.md`。

## 1. systemd user units

`Linger=yes` 开机自启，无需登录：

- **`tts-erp.service`**：uvicorn API，cwd=仓库根，`EnvironmentFile=.env`
- **`tts-erp-sync.service`**：APScheduler worker，安装脚本 `prod-switch/install-sync-worker.sh`
- **`tts-erp-watchdog.timer`**：每 10min 巡检 → `logs/watchdog.log`

## 2. 目录结构

```text
tts_erp_v2/
├── app.py               # FastAPI build_app() 工厂（中间件顺序见 §6）
├── api/v2/              # 路由：commerce / linkage / reporting / pages / spu_images / auth /
│                        #   llm_context / admin（rate-limit / purge-plugin-data / shops 注册：插件店铺人工登记进
│                        #   commerce.shops，data_source='plugin'（枚举 api|plugin），仅服务查询关联，readwrite 角色） /
│                        #   analytics（插件广告 dump ingest：/v2/analytics/sync/* → 落 plugin.* schema）
├── middleware/          # auth.py（角色矩阵）、session_auth.py、rate_limit.py、access_log.py
├── proxy/               # 出站层：tts_shop/（TikTok 签名+客户端）、miaoshou/、token_service.py
├── jobs/                # 同步 job 实现：tiktok/*、miaoshou/*、
│                        #   reporting（cost_snapshots 6h / profit_daily 1h）、token_refresh（6h）、
│                        #   ad_merge_today2daily（plugin.ad_merge_today2daily，ad_today→ad_daily 跨天固化）、runner
├── sync_worker/         # APScheduler；JOBS 注册表 + 调度状态（顶部 NOTE，以它为准）
├── db/models/           # 11 schema SQLAlchemy 模型 — plugin.py 为插件 dump 的全部 12 张表：
│                        #   订单/物流/结算 7 张（orders/order_lines/shipments/tracking_events/settlements/
│                        #   settlement_details/raw_log，原 chrome_sync.py）+ 广告 5 张（ad_today/ad_daily/
│                        #   ad_monthly/ad_raw_log/plugin_logs，原 analytics.py）
├── plugin/orders/       # 插件 dump 数据访问层：解析 TikTok 响应 + upsert 到 plugin.*
│                        #   （订单/物流/结算；原 tts_erp_v2/chrome_sync/）
├── plugin/ads/          # 插件广告 dump 数据访问层：coverage 查询 / upsert ad_* / plugin_logs
│                        #   （原 tts_erp_v2/analytics/{domain,repository}.py）
├── analytics/ linkage/ reporting/ storage/
│                        # analytics/ 只留读侧（spu_roi.py ROI 看板，读 plugin.ad_*）
└── static/

miaoshou/                # 妙手 SDK 包（独立包：client + miaoshou_signing.py；无 HTTP 路由，进程内用）
api_keys.py              # key 管理 CLI     schema_tts_erp.sql   restart.sh
tests/                   # v2 测试（api/jobs_*/linkage/middleware/proxy/reporting/storage/sync_worker）
tech-doc/                # 设计文档（external-api.md 端点活契约；analytics/；test-domains.md）
setup/                   # 用户向 setup 文档（tts-erp.md / analytics-sync.md）

根目录：conftest.py（pytest 路径引导）· .env（0600，勿 commit）· handoff.md（跨 session 交接）·
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

11 schema SQLAlchemy 模型，plugin.py 包含插件 dump 的全部 12 张表：

- 订单/物流/结算 7 张：orders、order_lines、shipments、tracking_events、settlements、settlement_details、raw_log
- 广告 5 张：ad_today、ad_daily、ad_monthly、ad_raw_log、plugin_logs

### 3.4 proxy/tts_shop/

TikTok 签名+客户端，内部处理 HMAC 签名 / `x-tts-access-token` / shop_cipher 位置 / 翻页 / 过期 token 续期。
