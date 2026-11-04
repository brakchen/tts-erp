# 媒体资产库与数字人 — 技术方案

> 状态：v1 已实现（2026-11-03）。
> 范围：在 tts-erp 内新增一个**全局**媒体资产库（图片 / 视频）与**数字人**（人设档案容器）。
> 上游依赖：MinIO 对象存储（已部署，`docker` 容器 `minio`，127.0.0.1:9000）。

## 1. 目标与边界

### 1.1 已确认需求（2026-11-03 用户拍板）

1. **数字人 = 人设档案容器**：名称 + 说明 + 状态 + 一组引用资产。不含 AI 生成能力。
2. **资产与数字人是多对多**：资产可以无归属（全局公共池），也可以同时挂到多个数字人。
3. **全局池**：资产不按店铺切分。
4. **上传通道**：浏览器预签名直传 MinIO（复用既有 `tts_erp_v2/storage/minio_client.py`）。
5. **预览**：图片直出、视频用浏览器 `<video>` 播放预签名 GET URL；服务端不生成缩略图（本机无 ffmpeg）。
6. **白名单与上限**：图片 jpeg/png/webp/gif ≤ 20 MiB；视频 mp4/webm/quicktime ≤ 200 MiB。
7. **存储桶**：新建独立 bucket `tts-erp-media`（`MEDIA_MINIO_BUCKET` 可覆盖）。
8. **页面形态**：两个独立页面「资产管理」「数字人」，权限点 `page:media-assets` / `page:digital-humans`，授权 admin + operator。

### 1.2 v1 非目标

- AI 生成（RunningHub / 换装视频）与本库的对接。
- 与 TikTok 视频发布流水线的联动（`publishing.video_publish_tasks` 仍独立可用）。
- 服务端缩略图 / 转码（无 ffmpeg）。
- 资产去重、版本管理、CDN、配额。

## 2. 架构

```text
浏览器
  │
  ├─ POST /v2/media/assets/upload-url ──> ttsERP ──> MinIO 预签名 PUT
  ├─ PUT  文件 ──────────────────────────────────────> MinIO (tts-erp-media)
  ├─ POST /v2/media/assets/{id}/confirm ──> ttsERP ──> HEAD 校验 size ──> ready
  │
  ├─ GET  /v2/media/assets[?kind=&keyword=&tag=&digitalHumanId=&excludeDigitalHumanId=]
  ├─ GET/PATCH/DELETE /v2/media/assets/{id}
  │
  └─ /v2/media/digital-humans[/{id}[/assets[/{assetId}]]]
```

对象字节流**从不经过 API 服务器**：浏览器直传 MinIO，服务端只保存元数据。

## 3. 数据模型（`media` schema，migration 0073）

| 表 | 含义 |
| --- | --- |
| `media.media_assets` | 一个上传的图片或视频。`kind`、`filename`、`content_type`、`size_bytes`、`bucket`、`object_key`、`status`、`tags[]`、软删 `deleted_at` |
| `media.digital_humans` | 一个人设档案。`name`、`description`、`status`（active/archived）、`deleted_at` |
| `media.digital_human_assets` | 数字人 ↔ 资产多对多链接。`UNIQUE(digital_human_id, asset_id)` |

约束与约定：

- `media_asset_status_check`：状态只有 `awaiting_upload` / `ready` / `failed`。
- `media_asset_size_check`：`size_bytes > 0`。
- 三张表都挂 `public.fn_touch_updated_at()` BEFORE UPDATE 触发器（仓库既有约定）。
- 资产删除是**软删**（`deleted_at`）+ 对象存储 best-effort 删除，**永不**删除数字人以外的资产行。
- 删除数字人只软删数字人并解绑链接，**资产本身保留在全局池**。

## 4. 对象键布局

```text
assets/<YYYY-MM-DD>/<asset_id>-<slug>.<ext>
```

- 无店铺段：库是全局的。
- 扩展名由**内容类型**决定（`CONTENT_TYPE_EXTENSIONS`），不由用户文件名决定——改名文件无法冒充别的类型。
- 桶由 `MEDIA_MINIO_BUCKET`（默认 `tts-erp-media`）选择，与 `tts-erp-spu-images`、`tiktok-video` 生命周期隔离。

## 5. 上传与确认

1. `POST /v2/media/assets/upload-url`：
   - 服务端先校验 `content_type` 在白名单内、`size_bytes` 不超过该类型的上限；不合规 → 400。
   - 插入 `awaiting_upload` 行 → 用行 id 组成对象键 → 回写 → 返回预签名 PUT URL。
2. 浏览器携带票据的 `required_headers`，PUT 文件到 MinIO。
3. `POST /v2/media/assets/{id}/confirm`：
   - HEAD 对象。对象不存在 → 标记 `failed`，返回 409 `UPLOAD_NOT_FOUND`。
   - `size` 与申报值不符 → 标记 `failed`，返回 409 `SIZE_MISMATCH`（防篡改上传）。
   - 一致 → 置 `ready`，返回预签名 GET URL。

MinIO 未配置时，资产和数字人详情仍可读取元数据，预览 URL 返回空值。未就绪的资产同样没有预览 URL。需要对象存储的上传和确认接口返回 HTTP 503。

## 6. 访问控制

| 端点 | 最低角色 |
| --- | --- |
| `GET /v2/media/**` | readonly |
| `POST / PATCH / DELETE /v2/media/**` | readwrite |

- 路由角色矩阵在 `tts_erp_v2/access/_policy.py`（`/v2/media/` 前缀：GET/HEAD/OPTIONS → readonly，其余 → readwrite）。
- 页面权限点 `page:media-assets` / `page:digital-humans` 由 `accounts/pages.py` 注册，alembic 0073 为 admin + operator 种子授权。
- Cookie 会话的所有写操作要求 `X-Requested-With: tts-erp`（CSRF 守卫）。
- 资产删除与数字人删除都走 `require_destructive_guard`（生产形状库名需 `ALLOW_PROD_DESTRUCTIVE=1`）。

## 7. 前端

两个 React + TypeScript + HeroUI 页面模块（沿用现有 MPA 模式）：

- `frontend/src/pages/media-assets/` — 上传（XHR 直传，带进度）、类型/关键字筛选、预览（`<img>` / `<video>`）、元数据编辑、删除。
- `frontend/src/pages/digital-humans/` — 创建/编辑/归档/删除数字人、挂载与解绑资产。

注册链路：`accounts/pages.py` → `alembic` 权限种子 → `frontend/src/app/page-loader.ts` → `api/v2/frontend_pages.py` `MIGRATED_PAGE_IDS` → `api/v2/pages.py` 路由。

前端只做展示性派生（字节数格式化）。授权、状态、筛选口径均由服务端决定。

## 8. 运维

- **本机前端包**：服务代码新增 React 页面后需重新构建并发布本机前端包（`bash scripts/build_frontend.sh`）。如果两页同时显示「页面加载失败」，先确认当前 `release.json` 对应的 Vite manifest 同时包含 `media-assets` 和 `digital-humans`。

- **新建 bucket**（一次性，加法操作）：

  ```bash
  docker exec minio mc mb --ignore-existing local/tts-erp-media
  ```

- **nginx 上传上限**：`~/setup/nginx/conf.d/services.conf` 的 `location /minio/` 段 `client_max_body_size` 从 `10m` 提到 `500m`（否则 200 MiB 视频会被 413 拒），然后 `nginx -t` + reload。
- **生产迁移与重启由人工执行**：本仓库不自动 `alembic upgrade`，不自动重启服务。

## 9. 测试

`tests/api/test_media_api.py`（21 例，isolated runner，真实 DB + 假对象存储）覆盖：

- 角色门（readonly 写入 403、readonly 读取 200）、内容类型与大小上限、图片/视频白名单。
- 上传票据 → confirm 成功、size 不符 409、未知资产 404。
- 列表筛选、未知 kind 400。
- 元数据编辑、软删并移除对象。
- 数字人 CRUD、归档、挂载/解绑幂等、挂载未知资产 404。

`frontend/src/pages/media-assets/contracts.test.ts`（12 例）覆盖 wire 解码与拒绝路径。

测试数据一律带 `TEST_` 前缀，并由本文件自带的清理 fixture 精确删除（`media` schema 尚未进入共享 `tests/api/conftest.py` 的 wipe 清单）。
