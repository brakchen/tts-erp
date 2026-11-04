# 媒体资产库与数字人 — 技术方案

> 范围包括 2026-11-03 的首版以及后续的操作流程调整。
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
  ├─ GET  /v2/media/assets[?kind=&status=&keyword=&tag=&digitalHumanId=&excludeDigitalHumanId=]
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
- 资产删除是**软删**（`deleted_at`）+ 对象存储 best-effort 删除。已删除资产从数字人的资产列表和计数中排除。
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
   - 分配 `awaiting_upload` 记录和对象键，签发 PUT URL 后一并提交。
2. 浏览器携带票据的 `required_headers`，PUT 文件到 MinIO。
3. `POST /v2/media/assets/{id}/confirm`：
   - HEAD 对象。对象不存在 → 标记 `failed`，返回 409 `UPLOAD_NOT_FOUND`。
   - `size` 与申报值不符 → 标记 `failed`，返回 409 `SIZE_MISMATCH`（防篡改上传）。
   - 一致 → 置 `ready`，可同时返回预签名 GET URL。已就绪的票据可以再次确认，沿用原入库时间。

MinIO 未配置时，资产和数字人详情仍可读取元数据，预览 URL 返回空值。未就绪的资产同样没有预览 URL。申请票据和首次确认需要对象存储，未配置时返回 HTTP 503。已就绪票据可再次确认，预览链接为空。

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

两页复用应用骨架、HeroUI 和同一个上传流程。

- **资产管理**用于上传、查找、预览和整理共享文件。选择文件后可以沿用原文件名，也可以改显示名称，点击上传后才传送文件。成功后打开详情，说明和标签在「编辑资产资料」中填写。保存的资料显示在所有引用处。要换用另一份文件，上传为新资产，再按需调整引用。
- **数字人**用于创建人设档案和组织它引用的素材。创建后打开新档案，从「选择已有资产」按名称、类型和预览挑选就绪素材，或者选择「上传新资产」。添加后继续留在同一数字人详情中。已有引用在选择器中有标记。
- **详情和编辑**分开。详情显示当前对象和状态。编辑中的输入只属于当前对象，提交失败可继续修改和重试，离开前可以保留或丢弃未保存的资料。新建或上传直接打开结果。若原列表有筛选，则返回默认列表并提示筛选已清除。
- **共享引用**在资产详情列出使用它的数字人，含归档档案。数字人素材可跳到共享资产详情。编辑显示名称、说明或标签会同步影响所有引用中的资料，文件内容保留。
- **归档和恢复**用于整理档案。归档将档案移到「已归档」筛选，保留引用，仍可查看、编辑及维护素材。恢复后回到「启用中」列表。
- **移除和删除**按作用范围区分。从数字人移除仅解除当前引用。删除数字人会移除档案及其引用，资产库的文件保留。从资产库删除会软删共享资产，让所有数字人失去该素材，确认前展示重新读取的使用者和影响。引用清单读取失败时可重试。
- **上传分阶段**。文件传输结束后进入确认入库，收到就绪响应才显示已入库。暂时确认失败可以继续确认原票据。数字人内上传已入库但添加失败时，显示资产已在资产库，并提供对同一资产重试添加的入口。

### 7.1 调查与取舍

只读走查使用本机 `http://127.0.0.1:9877`，前端资源来自 `build-31b5ca466a7e412be589`。从调查基线 `601e4ac5` 重新构建得到 `build-ae72960a51d0b15398a6`。现场详情仍用「删除」按钮，基线源码已改为「删除数字人」确认流程，现场和源码版本分开记录。资产库显示选文件入口和空列表。数字人详情展开后直接出现可编辑表单，添加入口要求输入资产 ID。选择文件即开始传输、确认失败后缺少可继续的票据以及草稿在刷新时可能重置，来自源码检查。

| 用户任务 | 比较与决定 | 验收 |
| --- | --- | --- |
| 上传一项素材 | 比较选文件即传和显式开始。采用显式开始，先让用户核对文件与名称，说明和标签留到入库后维护。 | 选择文件没有请求。传输完成后显示确认入库。确认故障重试沿用同一票据。 |
| 给数字人添加素材 | 比较跳去资产库后返回和留在档案内选择。采用档案内入口，保留目标名称，避免记忆目标和丢失上下文。 | 可以按名称、类型和预览选择。上传后的关联失败可单独重试，资产库只有一项新素材。 |
| 查看、编辑和离开 | 比较列表尾部内联表单和独立对象详情。采用详情内显式编辑，详情与草稿均指向同一个对象。 | 保存失败保留草稿。放弃 A 后打开 B，不出现 A 的字段或写入。 |
| 整理旧数字人 | 比较冻结式归档和可恢复的分类。采用后者，沿用现有维护能力与引用规则。 | 归档与恢复改变列表归属，现有引用不变。 |
| 确认影响范围 | 用引用者的名称和状态显示共享关系，按对象区分三个操作。 | 解绑保留其他引用，删除人设保留资产，删除资产后各人设引用列表一致。 |

首次隔离浏览器走查复现了错误未及时向辅助技术播报，以及 375px 宽度下长名称撑出页头和提示。后续为错误增加播报语义，让共享页头账号名和两个模块中的操作反馈在窄屏换行。另将写入成功和详情读取失败分开反馈，打开全局删除确认时重新核对引用清单。

### 7.2 验收脚本

在隔离环境中使用每轮唯一的 `TEST_` 名称。浏览器检查交互，API 和数据库检查持久化及共享关系。记录构建标识、提交、环境和失败注入位置。

| 场景 | 操作和可观察结果 |
| --- | --- |
| 独立上传 | 在资产库选择图片、开始上传，看到确认入库和最终详情。预览成功。存在不匹配筛选时仍能看到结果。 |
| 整理和查找 | 编辑名称、说明、标签。保存后在列表、详情与数字人引用处读取相同资料。按新名称或标签找到资产。 |
| 创建与添加已有素材 | 创建两份数字人档案。两次用名称搜索、预览并添加同一资产。重新打开选择器确认已有标记，资产详情显示两个引用者。 |
| 在档案内上传 | 上传第二个文件，分别核对入库和引用。首次关联失败后只重试关联，请求及记录均无重复创建。 |
| 移除、归档和删除 | 在一份档案移除共享素材，另一份继续使用。归档和恢复后核对引用。删除档案后文件仍可在资产库打开。删除共享文件后确认其他档案不再显示该项。 |
| 故障与返回 | 延迟确认、拒绝首次保存、编辑 A 后尝试离开并打开 B。检查确认中的反馈、输入保留、对象隔离、清除筛选、无权限、预览失败、空状态及读取失败重试。 |

### 7.3 候选版本验收

2026-11-05，隔离测试虚拟机验证了前端包 `build-f21147e13b65a9b625f1`（产品代码 `766a1086`，桌面断言复跑 `c0051fce`）。30 项媒体 API 用例、20 项媒体组件与契约用例、3 项 Chromium 场景通过。

- 桌面用例实际完成独立上传、预览、建档、两份档案复用同一素材、名称与标签保存、解绑、归档恢复、删除档案以及全局删除。验收时核对了页面与数据库中的状态。
- 375px 的 `/tts` 用例完成确认延迟和失败、入库后关联失败、对原记录分别重试、读取和预览重试、保留及放弃草稿。请求计数确认只申请一个上传票据并传输一次。另用实际只读会话验证两个页面返回 403。
- 独立验收 Agent 检查了 375px、1440px 和 2560px 页面截图。现场旧包走查、隔离 Chromium 验收与真实用户试用属于不同证据。浏览器验收使用受控存储。

## 8. 运维

- **本机前端包**：服务代码新增 React 页面后需重新构建并发布本机前端包（`bash scripts/build_frontend.sh`）。如果两页同时显示「页面加载失败」，先确认当前 `release.json` 对应的 Vite manifest 同时包含 `media-assets` 和 `digital-humans`。

- **新建 bucket**（一次性，加法操作）：

  ```bash
  docker exec minio mc mb --ignore-existing local/tts-erp-media
  ```

- **nginx 上传上限**：`~/setup/nginx/conf.d/services.conf` 的 `location /minio/` 段 `client_max_body_size` 从 `10m` 提到 `500m`（否则 200 MiB 视频会被 413 拒），然后 `nginx -t` + reload。
- **生产迁移与重启由人工执行**：本仓库不自动 `alembic upgrade`，不自动重启服务。

## 9. 测试

- `tests/api/test_media_api.py` 使用真实隔离数据库与受控对象存储，覆盖角色门槛、票据签名失败回滚、确认重试及并发结果、搜索筛选、共享引用、归档恢复、解绑及删除。
- `frontend/src/pages/media-assets/contracts.test.ts` 和 `workflows.test.tsx` 覆盖 wire 契约及界面状态。组件测试由 `tests/frontend/test_components.py` 通过隔离入口调用。
- `tests/browser/test_media_workflows.py` 操作本次构建的 Chromium 页面。浏览器请求转交同进程 FastAPI，使用真实会话权限与临时数据库。文件传输和读取使用受控字节存储，验证浏览器动作、预览、失败恢复及数据结果。真实 MinIO 网络与 CORS 属于运行环境验证。

窄集通过 `bash scripts/test_isolated.sh fast tests/api/test_media_api.py tests/frontend/test_components.py tests/browser/test_media_workflows.py` 执行。快速基线使用 `bash scripts/test_baseline.sh`。
