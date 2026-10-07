# TikTok 视频发布：技术设计

> **文档导航**：[产品方案](01-product-proposal.md) · [技术设计](02-technical-design.md) · [实施计划](03-implementation-plan.md) · [测试计划和结果](04-test-plan-and-results.md)

> 来源基线：`docs/video-publish-design@b88b26c` 中的 `docs/design/tiktok-video-publish.md`。
> 本目录按职责拆分原 2781 行单体文档；四个文件共同构成当前项目文档集。

## 文档职责

本文是架构与契约的单一真相源，覆盖领域模型、状态机、对象生命周期、Worker、API、前端状态、可观测性与安全边界。产品目标冲突时先修订《产品方案》；实现偏离本文时必须在《实施计划》中记录迁移，而不是在调用方继续增加兼容分支。

## 当前需要先决策的契约

1. **设备/本地清理失败时是否允许重新排队**：必须在“API 直接 409”与“允许排队但由领取门禁阻塞”之间选定一个模型，并统一 `allowed_actions`、写路径、数据库约束与测试。
2. **运营指标作用域**：`pollState` 是 owner-scoped 还是全局指标，必须明确。
3. **队列位置定义**：必须与 Worker 的真实可领取条件一致。
4. **条件请求缓存**：ETag 与对应 payload 必须由同一缓存条目持有；没有 payload 时不得接受 304 作为可渲染结果。

## 3. 总体架构

```text
浏览器
  │
  ├─ POST 创建上传票据（视频元数据 + 文案 + client_request_id）
  │        ├─ INSERT publishing.video_publish_tasks
  │        └─ 返回 MinIO 预签名 PUT URL
  │
  ├─ PUT 视频 ───────────────────────────────────────────→ MinIO
  │
  └─ POST confirm-upload
           ├─ HEAD 校验 size/content-type/ETag
           └─ task → pending/queued

独立 tts-erp-publish worker
  │
  ├─ PostgreSQL 原子领取全局唯一任务
  ├─ MinIO 下载到本地 spool，校验对象
  ├─ ADB 推送到 /sdcard/Movies/TTSERP/tts_erp_<execution_generation>.mp4
  ├─ MediaStore 扫描并确认相册可见
  ├─ POST Artemis /api/run（固定 session_id + device_serial）
  ├─ GET /api/sessions/{session_id} 持续查询
  ├─ 必要时 GET /api/sessions/{session_id}/steps 辅助分类
  ├─ 结果不确定时创建 verify attempt 自动核验
  └─ 成功后清理设备、本地 spool 与 MinIO 对象
```

### 3.1 为什么使用独立发布 Worker

视频发布可能持续数分钟，且涉及 ADB、Artemis 轮询和手机状态。它不应运行在 FastAPI 请求生命周期中，也不应占用现有 TikTok 数据同步作业。

新增独立进程：

```text
tts-erp.service          # HTTP API / 页面
tts-erp-sync.service     # 既有数据同步
tts-erp-publish.service  # 视频发布调度与恢复
```

入口建议：

```bash
python -m tts_erp_v2.publishing.worker
```

Worker 每次只领取一个任务，使用短事务更新状态；等待 Artemis 时不得持有数据库事务或行锁。
## 4. 数据模型

新增 `publishing` schema。

### 4.1 `publishing.video_publish_tasks`

一行代表一个用户原始发布任务：

```sql
CREATE TABLE publishing.video_publish_tasks (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id             uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    client_request_id     uuid NOT NULL UNIQUE,
    created_by_user_id    bigint,
    created_by_key_hash   text,

    caption               text NOT NULL,
    original_filename     text NOT NULL,
    object_filename       text NOT NULL,
    content_type          text NOT NULL,
    size_bytes            bigint NOT NULL,

    object_bucket         text NOT NULL,
    object_key            text NOT NULL UNIQUE,
    object_generation     uuid NOT NULL DEFAULT gen_random_uuid(),
    object_upload_expires_at timestamptz,
    object_etag           text,
    object_sha256         text,
    object_uploaded_at    timestamptz,
    object_deleted_at     timestamptz,

    cleanup_intent        text NOT NULL DEFAULT 'none',
    cleanup_lease_owner   text,
    cleanup_lease_expires_at timestamptz,
    cleanup_heartbeat_at  timestamptz,

    status                text NOT NULL,
    stage                 text NOT NULL,
    stage_started_at      timestamptz NOT NULL DEFAULT now(),
    attempt_count         integer NOT NULL DEFAULT 0,
    publish_budget_used   integer NOT NULL DEFAULT 0,
    next_attempt_at       timestamptz,

    lease_owner           text,
    lease_expires_at      timestamptz,
    heartbeat_at          timestamptz,
    row_version           integer NOT NULL DEFAULT 1,

    target_device_serial  text NOT NULL,
    target_app_package    text NOT NULL,
    execution_generation  uuid UNIQUE,
    device_path           text,
    spool_path            text,

    last_error_code       text,
    last_error_message    text,

    device_cleanup_status text NOT NULL DEFAULT 'not_started',
    device_cleanup_error  text,
    device_cleanup_attempts integer NOT NULL DEFAULT 0,
    device_cleanup_next_attempt_at timestamptz,
    spool_cleanup_status  text NOT NULL DEFAULT 'not_started',
    spool_cleanup_error   text,
    spool_cleanup_attempts integer NOT NULL DEFAULT 0,
    spool_cleanup_next_attempt_at timestamptz,
    object_cleanup_status text NOT NULL DEFAULT 'not_started',
    object_cleanup_error  text,
    object_cleanup_attempts integer NOT NULL DEFAULT 0,
    object_cleanup_next_attempt_at timestamptz,

    queued_at             timestamptz,
    started_at            timestamptz,
    completed_at          timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);
```

`status`：

```text
pending | running | succeeded | failed | needs_review | cancelled
```

`stage`：

```text
awaiting_upload
queued
waiting_device
downloading
staging_device
dispatching_artemis
waiting_artemis
verifying
done
```

`cleaning` 仅是 0058 迁移识别并归一化的旧值；当前 status/stage 组合约束不允许
`running/cleaning`，业务结果直接进入终态 `*/done`，随后由独立 cleanup lease 执行资源清理。

三个 `*_cleanup_status` 使用同一取值：

```text
not_started | pending | succeeded | failed
```

设备、spool 和 MinIO 清理分开记录，避免一个聚合字段掩盖仍残留在哪个位置。

全局只允许一个运行任务：

```sql
CREATE UNIQUE INDEX uq_video_publish_one_running
ON publishing.video_publish_tasks ((1))
WHERE status = 'running';
```

### 4.2 `publishing.video_publish_attempts`

一行对应一个 Artemis session。正式发布重试与自动核验都写入该表：

```sql
CREATE TABLE publishing.video_publish_attempts (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id               bigint NOT NULL
                          REFERENCES publishing.video_publish_tasks(id)
                          ON DELETE RESTRICT,
    sequence_no           integer NOT NULL,
    kind                  text NOT NULL,       -- publish | verify
    related_attempt_id    bigint
                          REFERENCES publishing.video_publish_attempts(id)
                          ON DELETE RESTRICT,

    artemis_session_id    uuid NOT NULL UNIQUE,
    status                text NOT NULL,
    prompt_version        text NOT NULL,
    prompt_snapshot       text NOT NULL,
    device_serial         text NOT NULL,
    device_path           text,
    target_app_package    text NOT NULL,
    artemis_profile       text NOT NULL,
    artemis_verification_level text NOT NULL,

    artemis_output        jsonb,
    artemis_error         text,
    steps_count           integer,
    submit_retry_count    integer NOT NULL DEFAULT 0,
    last_polled_at        timestamptz,
    retry_classification  text,
    retry_safe            boolean,

    submitted_at          timestamptz,
    started_at            timestamptz,
    finished_at           timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),

    UNIQUE (task_id, sequence_no)
);
```

约束同一任务只能有一条活跃执行：

```sql
CREATE UNIQUE INDEX uq_video_publish_task_active_attempt
ON publishing.video_publish_attempts (task_id)
WHERE status IN ('created', 'submitting', 'queued', 'running', 'unknown');
```

`related_attempt_id` 用于表达“这次 verify 在核验哪次 publish”。

示例：

```text
任务 #1004
├── sequence 1 / publish / session A → failed（结果不确定）
└── sequence 2 / verify  / session B → 未找到发布，任务保守进入 needs_review/done
```

两条 Artemis 历史全部保留；单次否定核验不创建后续 publish attempt。

### 4.3 `publishing.worker_heartbeats`

API 与页面需要知道独立 Worker 是否存活；空闲时没有 task lease，不能只看任务表。增加轻量心跳表：

```sql
CREATE TABLE publishing.worker_heartbeats (
    instance_id   text PRIMARY KEY,
    hostname      text NOT NULL,
    pid           integer NOT NULL,
    status        text NOT NULL, -- starting | ready | stopping
    device_status text NOT NULL DEFAULT 'unknown',
    device_message text,
    version       text,
    started_at    timestamptz NOT NULL,
    heartbeat_at  timestamptz NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
```

Worker 每 5 秒 upsert 自己的行；API 以 `heartbeat_at >= now() - interval '15 seconds'` 判定进程可用。`device_status/device_message` 是 Worker 对配置 serial、ADB 在线/解锁、TikTok package、Artemis、MinIO 及设备清理占用的有界综合探测；设备 offline/locked/busy 不阻止有权限用户创建排队任务，Worker 不可用或必需配置缺失才写阻断原因。超过 5 分钟的终止实例可由 Worker 自身或运维清理。
## 5. 上传与对象生命周期

### 5.1 上传模式

复用现有 `tts_erp_v2/storage/minio_client.py` 与 SPU 图片的预签名上传模式：

1. ttsERP 创建 `awaiting_upload` 任务并生成 object key；
2. 浏览器直接 PUT 到 MinIO；
3. 浏览器调用 confirm；
4. ttsERP 通过 HEAD 校验对象并保存 ETag；
5. 校验通过后进入 `queued`。

object key：

```text
video-publish/YYYY/MM/<task_public_id>/<sanitized_filename>.mp4
```

数据库保存 `object_bucket + object_generation + object_key + object_etag`，并持久化该 generation 已签发 PUT ticket 的最晚 `object_upload_expires_at`；预签名 URL 本身只在响应中短暂存在。初次上传和每次 replacement 都生成从未复用的新 generation/key。既有任务的每次 PUT ticket 签发先锁定 task row，锁内校验 stage/generation、签名并把 expiry 以单调 `max` 持久化后才返回 URL；失去 stage 的请求不暴露已签 URL而返回结构化 conflict/retry。票据 TTL 不得超过 MinIO/SigV4 的 7 天硬上限。取消、保留期和 replacement 的对象清理使用 PostgreSQL 时间；selector 除 `object_cleanup_next_attempt_at` 外独立要求 `object_upload_expires_at + 15 minutes completion grace <= clock_timestamp()`。cleanup work 快照并只删除退休 generation 的 key/已确认 ETag；missing object 同样保持 pending 到该屏障后执行幂等 absence/delete，旧 cleaner 永远不能触及新 generation。MinIO 必须为 ttsERP 页面来源配置仅允许 PUT/HEAD 所需 header 的 CORS；浏览器 URL 通过既有 `MINIO_PUBLIC_HOST` 重写，不能把服务器内部 `127.0.0.1` 地址返回给远端浏览器。

### 5.2 校验

v1 仅接受 MP4。具体大小上限由服务端配置并通过配置接口下发，前端只做提前提示，服务端始终重新校验：

- 扩展名 `.mp4`；
- `Content-Type: video/mp4`；
- `0 < size_bytes <= configured_max_size`；
- confirm 时对象存在且实际 size 与创建任务时一致；
- 下载后计算摘要；单段 ETag 可校验 MD5，多段 ETag 依赖记录的 SHA-256 或 MinIO SDK 完整性检查。

### 5.3 清理规则

| 结果 | 手机暂存 | 本地 spool | MinIO |
| --- | --- | --- | --- |
| 成功 | 删除 | 删除 | 删除 |
| 明确失败、允许重试 | 删除 | 删除 | 保留 |
| 结果不确定、等待核验 | 保留至核验结束或安全隔离 | 删除 | 保留 |
| 用户取消未运行任务 | 无 | 无 | 删除 |
| 清理失败 | 后台重试 | 后台重试 | 后台重试 |

确认发布后先尝试清理设备文件，再释放设备执行槽。设备清理必须只针对服务端生成的精确 `device_path`：删除文件、删除/重扫该 MediaStore entry，并以同一路径轮询查询直至目录项与 MediaStore 行都不存在；确认前失败保持 `device_cleanup_status=failed`。设备预检同时检查文件系统与 MediaStore 中的受管视频残留；任一处未清除时，下一任务停在 `waiting_device`，不能在同一相册继续 staging。一个登记的 spool resource 同时包含精确 `video.mp4` 与其精确 sibling `video.mp4.part`；selector-owned cleanup 必须逐个删除并验证两者，不得使用 wildcard。spool 与 MinIO 清理可在业务成功后后台重试。任一清理失败不能覆盖 `status=succeeded`。
## 6. 调度、设备与 Artemis 契约

### 6.1 任务领取

Worker 以 `queued_at, id` 排序领取：

```sql
SELECT id
FROM publishing.video_publish_tasks
WHERE status = 'pending'
  AND stage = 'queued'
  AND (next_attempt_at IS NULL OR next_attempt_at <= now())
ORDER BY queued_at, id
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

同一事务内将任务改为 `running/downloading`。数据库部分唯一索引是跨进程最终护栏，进程内 `max_instances=1` 只能作为附加保护。

### 6.2 设备预检与暂存

正式调用 Artemis 前执行：

1. `adb devices` 确认目标 serial 在线；
2. 确认 `com.zhiliaoapp.musically` 已安装；
3. 下载视频到配置的 spool 目录，使用 `.part` 后原子 rename；
4. 推送到：

   ```text
   /sdcard/Movies/TTSERP/tts_erp_<task_public_id>.mp4
   ```

5. 比较设备文件大小；
6. 触发 MediaStore 扫描；
7. 查询 MediaStore，确认目标文件在 TTSERP 相册可见；
8. 同时查询文件系统与 MediaStore，确认同一受管相册没有会干扰选择的其他 `tts_erp_*.mp4` 残留。

ADB adapter 只暴露受控方法：

```python
check_device(serial)
check_package(serial, package)
stage_video(serial, local_path, device_path)
verify_media_visible(serial, device_path)
remove_staged_video(serial, device_path)
```

不得提供接收任意用户字符串的 `shell(command)` 公共接口。

### 6.3 指定设备提交

Artemis `POST /api/run` 已支持 `device_serial`。显式指定的 serial 不会被静默替换；未知/离线设备会拒绝，锁屏设备会返回 409。

```json
{
  "goal": "<固定 Prompt>",
  "session_id": "<attempt artemis_session_id>",
  "profile": "pro",
  "device_serial": "D123084100AC",
  "locked_app_package": "com.zhiliaoapp.musically",
  "verification_level": "strict",
  "ingress": "tts-erp-video-publish"
}
```

同一执行记录的网络重试必须复用同一个 `session_id`。只有已确认前一条执行记录终止且允许业务重试时，才生成新的 session ID。

### 6.4 状态查询

通过仓库内 `publishing.artemis_client.ArtemisClient` 的受控 `httpx` adapter；提交和恢复都复用 attempt 快照中的同一 UUID：

```python
result = await client.submit(..., session_id=attempt.artemis_session_id)
result = await client.get_task(attempt.artemis_session_id)
```

底层接口：

```text
GET /api/sessions/{session_id}        # 单任务持久化状态
GET /api/status                       # 实时队列与 active task fallback
GET /api/sessions/{session_id}/steps # 执行步骤与失败位置
GET /api/devices                      # 设备在线/忙碌状态
```

终态：

```text
completed | success | failed | cancelled | canceled | rejected
```

等待超时不是业务失败。Worker 只结束本轮等待，保留同一 attempt 为 `running`，下一轮继续查询同一 session。
## 7. 重试与自动核验

### 7.1 两类重试

**传输重试**：提交请求超时或响应丢失。

- 不创建新 attempt；
- 复用原 `artemis_session_id`；
- 先查询 session，再以相同 ID 重提；
- 依靠 Artemis 幂等 admission 防止重复执行。

**业务重试**：Artemis 已明确结束，且系统确认没有发生发布。

- 原 `video_publish_tasks` 不变；
- 新建 `kind=publish` attempt；
- 使用新的 `artemis_session_id`；
- 重新暂存视频并执行。

### 7.2 自动重试分类

可以自动重试的安全情况：

- MinIO 下载失败；
- ADB 推送或 MediaStore 确认失败；
- 设备离线/锁屏，Artemis 尚未 admission；
- Planner 失败且 `/steps` 为 0。

即使单次 verify attempt 返回 `not_published`，也不能自动重排 publish：平台可能仍在处理已点击发布的内容，任务必须进入 `needs_review/done`，之后只能由用户显式再次核验。

不得直接自动重试的情况：

- Artemis 在进入发布确认页面后失联；
- 步骤中已经出现最终“发布”点击，但 session 未成功终止；
- session 与全局 `/api/status` 都无法确认去向；
- 自动核验无法明确判断作品是否存在。

默认最多自动正式发布 3 次；退避由 `next_attempt_at` 控制。设备暂时不可用不应快速消耗重试次数。

### 7.3 自动核验

不确定时创建 `kind=verify` 的 Artemis session。核验 Prompt 只允许：

1. 打开 TikTok；
2. 进入当前账号的作品页和必要时的草稿箱；
3. 根据目标文案、最新发布时间与缩略图核对最新内容；
4. 返回“已发布 / 明确未发布 / 无法判断”；
5. 禁止进入上传页面，禁止点击发布。

处理：

```text
已发布     → task succeeded → 清理
明确未发布 → task needs_review/done；单次 `not_published` 不得自动 publish/queued
无法判断   → task needs_review/done
```

人工只处理 `needs_review`，不参与普通失败与超时。
## 8. 固定 Prompt 契约

Prompt 在 `tts_erp_v2/publishing/prompt.py` 中版本化；用户不能编辑原始 Prompt。

```python
PUBLISH_PROMPT_VERSION = "tiktok-video-publish-v1"
VERIFY_PROMPT_VERSION = "tiktok-video-verify-v2"
```

文案以 JSON 字符串嵌入，明确声明为数据而非指令，保留换行、emoji 和话题标签。每次执行把最终 Prompt 快照写入 attempt，便于审计和复现。

发布 Prompt 必须包含：

- App 名称与 package；
- TTSERP 专用相册与精确设备文件名；
- 文案 JSON 字符串；
- 只选择一个视频；
- 最终发布按钮最多点击一次；
- 找不到目标视频时停止，不能选择其他视频；
- 成功与失败的可观察条件；
- 不修改账号设置、不离开锁定 App。

核验 Prompt 必须明确禁止任何发布动作，并包含 caption、原始 basename、已确认 ETag/SHA 身份、相关 publish attempt 的预期最新发布时间窗，以及比较最新作品发布时间和缩略图的指令；任一识别证据不足时必须返回 `inconclusive`。
## 9. HTTP API

所有浏览器写请求继续发送：

```http
X-Requested-With: tts-erp
```

### 9.1 页面启动配置

```http
GET /v2/video-publish/config
```

响应：

```json
{
  "acceptedContentTypes": ["video/mp4"],
  "maxVideoBytes": 524288000,
  "maxCaptionCharacters": 4000,
  "target": {
    "appName": "TikTok",
    "appPackage": "com.zhiliaoapp.musically",
    "deviceSerialMasked": "D123…00AC",
    "album": "TTSERP"
  },
  "device": {
    "status": "ready",
    "message": "设备在线且空闲"
  },
  "canWrite": true
}
```

限制值由服务端配置生成；前端不得复制另一套常量。

### 9.2 创建任务与上传票据

```http
POST /v2/video-publish/tasks
```

```json
{
  "clientRequestId": "<browser generated UUID>",
  "filename": "video.mp4",
  "contentType": "video/mp4",
  "sizeBytes": 15393429,
  "caption": "..."
}
```

成功返回 201：

```json
{
  "taskId": "<public UUID>",
  "status": "pending",
  "stage": "awaiting_upload",
  "upload": {
    "url": "<presigned PUT>",
    "method": "PUT",
    "headers": {"Content-Type": "video/mp4"},
    "expiresAt": "2026-10-04T10:00:00Z"
  }
}
```

`clientRequestId` 唯一；双击或网络重放返回同一个任务，不重复建单。

### 9.3 刷新上传票据

```http
POST /v2/video-publish/tasks/{task_id}/upload-url
```

仅 `awaiting_upload` 可调用。用于预签名 URL 过期或浏览器重新选择原文件后续传完整对象。

### 9.4 确认上传并入队

```http
POST /v2/video-publish/tasks/{task_id}/confirm-upload
```

后端 HEAD 校验对象成功后原子更新为：

```text
status=pending, stage=queued, queued_at=now()
```

### 9.5 当前任务、列表与详情

```http
GET /v2/video-publish/tasks/current
GET /v2/video-publish/tasks?status=&limit=30&cursor=
GET /v2/video-publish/tasks/{task_id}
```

`/current` 是定时刷新使用的轻量端点，返回当前运行任务、最新 attempt、服务端时间和下一建议刷新间隔；无运行任务时返回 `task: null`。列表只返回摘要，不携带完整 Prompt 或大段 Artemis output；详情返回任务、清理状态和全部 attempts。

当前任务和列表摘要都必须返回可直接检索的 Artemis ID：

```json
{
  "taskId": "<ttsERP task UUID>",
  "status": "running",
  "stage": "waiting_artemis",
  "currentAttempt": {
    "kind": "publish",
    "sequenceNo": 2,
    "artemisSessionId": "c8af5a6c-4158-49c3-9141-13bc69b391d2",
    "status": "running"
  },
  "latestArtemisSessionId": "c8af5a6c-4158-49c3-9141-13bc69b391d2",
  "updatedAt": "2026-10-04T10:42:15Z"
}
```

尚未创建 Artemis attempt 的排队任务返回 `latestArtemisSessionId: null`，前端显示“尚未创建”，不得把 ttsERP task ID 冒充为 Artemis ID。三个 GET 端点支持 `ETag` / `If-None-Match`；无变化时返回 304，降低定时刷新负载。

### 9.6 操作

```http
POST /v2/video-publish/tasks/{task_id}/cancel
POST /v2/video-publish/tasks/{task_id}/retry
POST /v2/video-publish/tasks/{task_id}/verify
POST /v2/video-publish/tasks/{task_id}/cleanup/retry
```

- `cancel` 只允许 `awaiting_upload/queued/waiting_device`；运行中的正式发布不提供普通取消按钮；
- `retry` 只允许明确可重试的 `failed`；
- `verify` 用于 `needs_review`，也可由 Worker 自动触发；
- `cleanup/retry` 只影响清理状态，不改变发布结果。

非法状态统一返回 409，并附服务端允许动作：

```json
{
  "detail": {
    "code": "TASK_ACTION_NOT_ALLOWED",
    "message": "该任务正在发布，不能重新提交。",
    "allowedActions": ["view"]
  }
}
```
## 12. 模块与文件布局

```text
tts_erp_v2/publishing/
├── __init__.py              # 窄 public interface
├── domain.py                # 状态、命令、outcome、转换规则
├── submission.py            # 创建票据、confirm、cancel
├── dispatcher.py            # dispatch_one / recover_active
├── repository.py            # claim 与持久化转换
├── prompt.py                # publish/verify 固定 Prompt
├── artemis_client.py        # 仓库内 httpx Artemis adapter
├── adb_device.py            # 受控 ADB adapter
├── object_store.py          # video bucket adapter
├── diagnostics.py           # 有界递归诊断清洗
├── observability.py         # 受控结构化事件
├── safe_values.py           # 共享 serial 掩码
└── worker.py                # 独立进程入口

tts_erp_v2/db/models/publishing.py
tts_erp_v2/db/models/__init__.py                  # 导出模型，确保 Alembic metadata 可见
tts_erp_v2/api/v2/video_publish.py
tts_erp_v2/api/v2/pages.py                       # /v2/pages/video-publish
tts_erp_v2/app.py                                # include_router
tts_erp_v2/access/_policy.py                     # GET readonly / 写操作 readwrite
tts_erp_v2/accounts/pages.py                     # page:video-publish 与角色授权
tts_erp_v2/templates/pages/video-publish.html
tts_erp_v2/static/js/video-publish.js
tts_erp_v2/static/css/video-publish.css
alembic/versions/0053_video_publish.py            # 实现时以实际 Alembic head 为准
scripts/systemd/tts-erp-publish.service
tests/publishing/                              # API/DB/adapter/Node 合同测试
```

公共业务入口保持窄：

```python
create_upload_ticket(...)
confirm_upload(...)
cancel_task(...)
retry_task(...)
request_verification(...)
dispatch_one(...)
recover_active(...)
```

FastAPI handler 只负责 wire 校验、鉴权和 outcome → HTTP 映射；不得自行拼 Prompt、运行 ADB 或决定状态转换。
## 13. 配置

```env
TIKTOK_PUBLISH_MINIO_BUCKET=tiktok-video
ARTEMIS_BASE_URL=http://127.0.0.1:8001
ARTEMIS_DEVICE_SERIAL=D123084100AC
ARTEMIS_APP_PACKAGE=com.zhiliaoapp.musically
ARTEMIS_PROFILE=pro
ARTEMIS_VERIFICATION_LEVEL=strict
TIKTOK_PUBLISH_ALBUM=TTSERP
TTS_ERP_PUBLISH_SPOOL_DIR=~/.local/share/tts-erp/video-publish
TIKTOK_PUBLISH_MAX_VIDEO_BYTES=524288000
TIKTOK_PUBLISH_MAX_ATTEMPTS=3
```

凭据、Artemis token 和 MinIO secret 只来自环境或既有 secret 管理；不得写入代码、Prompt、浏览器响应或日志。
## 14. 故障恢复

Worker 启动时执行 `recover_active()`：

1. 找到 `status=running` 的唯一任务；
2. 找到活跃 attempt；
3. 若已有 Artemis session，先查询 session，不重新创建；
4. 若 session 运行中，恢复轮询；
5. 若 session 已终止，按结果推进；
6. 若尚未提交 Artemis，根据 stage 检查 spool/设备文件后继续或安全回退；
7. 若 lease 过期但无法确定最终发布动作，创建 verify attempt；
8. spool 与 MinIO 清理独立重试，不阻塞新的发布任务；设备清理失败则在下一次 staging 前形成 readiness gate，清除残留后才能继续。

进程被终止、HTTP 超时或数据库短暂不可用，都不能通过“新建另一个业务任务”恢复。
## 17. 核心决策汇总

1. ttsERP 管理业务任务；Artemis 只执行手机操作。
2. 浏览器直传 MinIO，但上传票据、对象确认和生命周期由 ttsERP 管理。
3. 数据库存 bucket/key/ETag，不存预签名 URL。
4. 一个业务任务对应多条 Artemis attempt；重试不复制业务任务。
5. `device_serial` 和 `locked_app_package` 同时传给 Artemis，设备不会静默切换。
6. 同一个 attempt 的网络重试复用 session ID；正式业务重试才生成新 session ID。
7. 超时后继续查询，不立即判失败；结果不确定先自动核验。
8. 全局唯一 running task 由数据库部分唯一索引保证。
9. 成功与清理分开建模；清理失败不会改写业务成功。
10. 前端的“单线发布轨道”直接表达全局串行约束；状态与允许操作全部由服务端真相驱动。
11. 页面提供智能或固定间隔定时刷新，任务、attempt 和 Artemis ID 的变化无需手工刷新即可出现。
12. 每条 Artemis attempt 的完整 session ID 都在详情中保留并可复制；列表展示当前或最近一次 ID。
## 18. 实现级数据字典

本节是开发时的字段真相源。数据库使用 `snake_case`；HTTP JSON 使用 `camelCase`。时间统一为 PostgreSQL `timestamptz`，API 输出 UTC ISO-8601，例如 `2026-10-04T10:42:15Z`。客户端不得提交服务端状态、计数、设备路径或 Artemis ID。

### 18.1 `video_publish_tasks` 字段

| 字段 | 类型/可空 | 写入方 | 含义与约束 |
| --- | --- | --- | --- |
| `id` | bigint, 非空 | DB | 内部主键，不暴露给普通前端。 |
| `public_id` | uuid, 非空唯一 | DB | ttsERP 任务 ID，出现在 URL、日志和 UI；不可与 Artemis ID 混用。 |
| `client_request_id` | uuid, 非空唯一 | 浏览器→API | 创建幂等键；同一用户重放相同请求返回原任务。全局唯一即可，响应需标记是否 replay。 |
| `created_by_user_id` | bigint, 可空 | API | 创建用户标识；当前是审计值、**非 FK**，API key 或历史导入可为空。 |
| `created_by_key_hash` | text, 可空 | API | API-key 创建者的不可逆 key hash；与 user id 共同用于 owner scope。 |
| `caption` | text, 非空 | 用户 | 原样发布文案；规范化 CRLF→LF、拒绝 NUL，不自动删运营备注。 |
| `original_filename` | text, 非空 | 用户 | 有界浏览器 basename，用于展示和续传时文件名/大小核对；不得直接拼接对象、本地或设备路径。 |
| `object_filename` | text, 非空 | API | 仅用于 object key 的 ASCII 安全 basename；与 `original_filename` 分开保存。 |
| `content_type` | text, 非空 | 用户+confirm | v1 固定 `video/mp4`；confirm 重新核对对象元数据。 |
| `size_bytes` | bigint, 非空 | 用户+confirm | 创建时声明，confirm 必须与 MinIO 实际大小相同。 |
| `object_bucket` | text, 非空 | API | 创建时从服务端配置快照，v1 为 `tiktok-video`。 |
| `object_key` | text, 非空唯一 | API | 服务端按 upload generation 生成；replacement 永不复用旧 key，用户不能指定。 |
| `object_generation` | uuid, 非空 | API | 当前上传 generation；初始上传与每次 replacement 都生成新值。 |
| `object_upload_expires_at` | timestamptz, 可空 | API | 当前 generation 所有已签发 PUT ticket 的最晚 PostgreSQL-time cleanup fence；对象清理在此之前不可领取。 |
| `object_etag` | text, 可空 | confirm | MinIO HEAD 返回值；上传完成前为空。cleanup work 在可用时绑定该值。 |
| `object_sha256` | text, 可空 | Worker | 下载后计算的内容摘要；用于诊断与后续重复视频识别，不作为 UI 操作 ID。 |
| `object_uploaded_at` | timestamptz, 可空 | confirm | HEAD 校验成功时间。 |
| `object_deleted_at` | timestamptz, 可空 | cleanup | MinIO 删除成功时间；删除幂等，404 也视为成功。 |
| `cleanup_intent` | enum text, 非空 | Repository | `none/finalize_success/requeue_publish/preserve_state`；决定资源清理后业务状态是否变化。 |
| `cleanup_lease_owner` | text, 可空 | Cleanup selector | 与 publish lease 正交的清理 owner。 |
| `cleanup_lease_expires_at` | timestamptz, 可空 | Cleanup selector | 数据库时间控制的清理租约截止。 |
| `cleanup_heartbeat_at` | timestamptz, 可空 | Cleanup selector | 慢外部删除期间持续更新的清理心跳。 |
| `status` | enum text, 非空 | Domain service | 业务状态，含义见 §18.4。 |
| `stage` | enum text, 非空 | Domain service | 当前细分阶段，含义见 §18.5。 |
| `stage_started_at` | timestamptz, 非空 | Repository | 当前 stage 的数据库时间起点。 |
| `attempt_count` | int, 非空 | Domain service | 追加式审计计数：已创建的 `kind=publish` 数量，永不因 admission refund 递减。 |
| `publish_budget_used` | int, 非空 | Domain service | 已消耗的正式发布预算；只有白名单内、已证明发生在 admission 前的设备 locked/busy 拒绝可退款。verify 不计入。 |
| `next_attempt_at` | timestamptz, 可空 | Dispatcher | 退避截止时间；为空表示可立即领取。 |
| `lease_owner` | text, 可空 | Worker | 当前领取实例 ID；业务终态和 pending 时必须为空。 |
| `lease_expires_at` | timestamptz, 可空 | Worker | 崩溃恢复租约；运行时每次 heartbeat 向后延长。 |
| `heartbeat_at` | timestamptz, 可空 | Worker | 当前任务最近一次执行心跳，便于识别卡死阶段。 |
| `row_version` | int, 非空 | Repository | 乐观并发版本；每次状态转换 `+1`，写入使用 compare-and-swap。 |
| `target_device_serial` | text, 非空 | API config | 创建时固定的 ADB serial 快照；普通用户不可修改。 |
| `target_app_package` | text, 非空 | API config | 创建时固定的 package 快照。 |
| `execution_generation` | uuid, 可空唯一 | Worker claim | 每轮下载/staging 前分配的不可变资源 generation；迁移前旧行可空。 |
| `device_path` | text, 可空 | Worker | `tts_erp_<execution_generation>.mp4` 精确 staging 路径。 |
| `spool_path` | text, 可空 | Worker | `<spool>/<task>/<execution_generation>/video.mp4`；ownership 同时覆盖 exact sibling `.part`。 |
| `last_error_code` | text, 可空 | Domain service | 面向机器的稳定错误码；成功重试后保留历史在 attempt，主表清空。 |
| `last_error_message` | text, 可空 | Domain service | 已清洗的运营可读错误；不得含密钥、预签名 URL 或堆栈。 |
| `*_cleanup_status` | enum text, 非空 | Cleanup service | 分别描述设备、spool、对象清理。 |
| `*_cleanup_error` | text, 可空 | Cleanup service | 对应位置最近一次清理错误；成功后清空。 |
| `*_cleanup_attempts` | int, 非空 | Cleanup service | 对应资源的清理尝试次数。 |
| `*_cleanup_next_attempt_at` | timestamptz, 可空 | Cleanup service | 数据库时间控制的下一次清理退避截止。 |
| `queued_at` | timestamptz, 可空 | confirm/retry | 最近一次进入发布队列时间，队列排序真相源。 |
| `started_at` | timestamptz, 可空 | Worker | 第一次进入 running 的时间，不因后续 verify 覆盖。 |
| `completed_at` | timestamptz, 可空 | Domain service | 进入 succeeded/failed/cancelled/needs_review 终止等待态的时间；重新排队时清空。 |
| `created_at` | timestamptz, 非空 | DB | 创建时间。 |
| `updated_at` | timestamptz, 非空 | DB trigger | 任意持久化变更时间，也用于 ETag。 |

### 18.2 `video_publish_attempts` 字段

| 字段 | 类型/可空 | 含义与约束 |
| --- | --- | --- |
| `id` | bigint, 非空 | 内部主键。 |
| `task_id` | bigint, 非空 | 父任务 FK，禁止级联删除历史。 |
| `sequence_no` | int, 非空 | 父任务内所有 Artemis 调用的递增序号；publish/verify 共用序列。 |
| `kind` | enum text, 非空 | `publish` 正式发布；`verify` 只读核验。 |
| `related_attempt_id` | bigint, 可空 | verify 必须指向同 task 的 publish；publish 为空。完整 submission identity 插入后由 0065 trigger 保证不可变。 |
| `artemis_session_id` | uuid, 非空唯一 | Artemis 任务 ID；在调用前由 ttsERP 生成并持久化。 |
| `status` | enum text, 非空 | attempt 生命周期，见 §18.6。 |
| `prompt_version` | text, 非空 | 固定模板版本，便于回溯行为变化。 |
| `prompt_snapshot` | text, 非空 | 本次实际提交文本；仅 admin/诊断权限可查看。 |
| `device_serial` | text, 非空 | 本次调用的精确设备 serial。 |
| `device_path` | text, 可空 | publish 对应设备文件；verify 通常为空。 |
| `target_app_package` | text, 非空 | 本次调用的精确 app package 快照。 |
| `artemis_profile` | text；active/new 非空，terminal legacy 可空 | 本次 submit/resubmit 的 profile 快照；0065 predecessor 终态审计行保留诚实 unknown/null，绝不猜默认值或恢复为 active。 |
| `artemis_verification_level` | text；active/new 非空，terminal legacy 可空 | 本次 submit/resubmit 的 verification level 快照；dispatch/resubmit 不读取可变 task/config。 |
| `artemis_output` | jsonb, 可空 | 有界、已清洗的 session 结果摘要；禁止无限保存全部 trace。 |
| `artemis_error` | text, 可空 | Artemis 返回的已清洗错误。 |
| `steps_count` | int, 可空 | 从 `/steps` 得到的数量；用于安全重试分类。 |
| `submit_retry_count` | int, 非空 | 相同 session ID 的传输重提次数，不增加业务 attempt。 |
| `last_polled_at` | timestamptz, 可空 | 最近查询 Artemis 状态时间。 |
| `retry_classification` | enum text, 可空 | 自动决策结果，见 §18.7。 |
| `retry_safe` | boolean, 可空 | `true` 明确未产生发布副作用；`false` 明确不可直接重试；`null` 尚未分类。 |
| `submitted_at` | timestamptz, 可空 | Artemis admission 成功或幂等确认时间。 |
| `started_at` | timestamptz, 可空 | Artemis 进入 running 的时间。 |
| `finished_at` | timestamptz, 可空 | attempt 进入终态时间。 |
| `created_at/updated_at` | timestamptz | 审计字段。 |

### 18.3 `worker_heartbeats` 字段

| 字段 | 含义 |
| --- | --- |
| `instance_id` | 启动时生成的 UUID，不复用 PID。 |
| `hostname` | 运行主机，排障时定位 systemd 实例。 |
| `pid` | 当前进程 PID，仅诊断。 |
| `status` | `starting/ready/stopping`。 |
| `device_status` | `ready/busy/offline/locked/unknown` 综合 readiness。 |
| `device_message` | 不含密钥/原始异常的有界运营说明。 |
| `version` | 部署版本或 git SHA。 |
| `started_at` | 本实例启动时间。 |
| `heartbeat_at` | 最近心跳；超过 15 秒视为 unavailable。 |
| `updated_at` | DB 更新时间。 |

### 18.4 `TaskStatus`

| 值 | 中文标签 | 终态 | 含义 |
| --- | --- | --- | --- |
| `pending` | 待处理 | 否 | 尚未占用全局执行槽；包含等待上传、排队和等待设备。 |
| `running` | 执行中 | 否 | 已领取全局执行槽，正在下载、staging、调用或核验。 |
| `succeeded` | 已发布 | 是 | 已确认 TikTok 发布成功；资源清理可仍在重试。 |
| `failed` | 失败 | 是/可重开 | 明确未成功且当前不再自动执行；是否可重试由 `allowedActions` 决定。 |
| `needs_review` | 需确认 | 是/可核验 | 自动核验仍无法判断；禁止直接普通重试。 |
| `cancelled` | 已取消 | 是 | 用户在产生发布副作用前取消。 |

### 18.5 `TaskStage`

| 值 | 适用 status | 含义 |
| --- | --- | --- |
| `awaiting_upload` | pending | 任务行已创建，MinIO 对象尚未 confirm。 |
| `queued` | pending | 对象已确认，等待领取。 |
| `waiting_device` | pending | 设备离线、锁屏、忙或 App 未就绪，稍后重试。 |
| `downloading` | running | 从 MinIO 下载并计算摘要。 |
| `staging_device` | running | ADB push、文件校验和 MediaStore 扫描。 |
| `dispatching_artemis` | running | attempt 已落库，正在幂等提交 `/api/run`。 |
| `waiting_artemis` | running | Artemis 已接收，持续轮询 session。 |
| `verifying` | running | verify attempt 检查作品页/草稿箱。 |
| `done` | succeeded/failed/needs_review/cancelled | 本轮业务流程结束；设备/spool/对象清理由正交 cleanup 状态继续表达。 |

`cleaning` 只作为 0058 识别的旧迁移输入保留在基础 stage 枚举中，当前合法 status/stage 组合不会产生 `running/cleaning`。

### 18.6 `AttemptStatus`

| 值 | 含义 |
| --- | --- |
| `created` | attempt 与 session ID 已持久化，尚未调用 Artemis。 |
| `submitting` | 正在提交；网络失败可用同一 ID 重提。 |
| `queued` | Artemis 已接收并排队。 |
| `running` | Artemis 正在控制设备。 |
| `success` | Artemis 报告 completed/success，或 verify 明确确认。 |
| `failed` | Artemis 明确失败。 |
| `rejected` | admission 被拒绝，例如设备不可用。 |
| `cancelled` | 明确由系统/管理员停止；不代表安全可重试。 |
| `unknown` | session 去向或发布副作用无法确定，必须分类/核验。 |

### 18.7 `RetryClassification`

| 值 | `retry_safe` | 后续动作 |
| --- | --- | --- |
| `pre_artemis_failure` | true | 原任务退避后重新排队，不创建无效 Artemis attempt。 |
| `admission_rejected` | true | 等设备恢复；若 session 未执行，可新建 publish attempt。 |
| `planner_zero_steps` | true | 在预算内自动创建新 publish attempt。 |
| `failed_before_publish_ui` | true | 在预算内自动重试。 |
| `publish_action_observed` | false | 不得直接重试，创建 verify attempt。 |
| `session_missing` | null | 继续查询 `/api/status`，超时后 verify。 |
| `verification_published` | false | 主任务成功。 |
| `verification_not_published` | null | 保守进入 `needs_review/done`；单次否定不得自动重试 publish，可由用户之后显式再次核验。 |
| `verification_inconclusive` | null | 主任务 `needs_review`。 |
| `retry_budget_exhausted` | false | 主任务 `failed`，不再自动重试。 |

### 18.8 `CleanupStatus`

| 值 | 含义 |
| --- | --- |
| `not_started` | 尚无对应资源或尚未进入清理。 |
| `pending` | 已计划或正在清理。 |
| `succeeded` | 删除成功；目标本来不存在也算成功。 |
| `failed` | 最近一次删除失败，可通过后台或页面重试。 |

### 18.9 `AllowedAction`

API 返回以下稳定字符串，前端只按返回值显示按钮：

| 值 | 按钮文案 | 允许场景 |
| --- | --- | --- |
| `view` | 查看详情 | 所有任务。 |
| `continue_upload` | 继续上传 | awaiting_upload。 |
| `cancel` | 取消任务 | awaiting_upload/queued/waiting_device。 |
| `retry` | 重试原任务 | failed 且对象仍存在、分类安全、预算允许。 |
| `verify` | 再次自动核验 | needs_review。 |
| `retry_cleanup` | 重试清理 | 任一 cleanup status=failed。 |
| `copy_artemis_id` | 复制 Artemis ID | 至少存在一个 attempt。 |
## 19. 合法状态转换

所有状态修改必须通过 domain transition 函数，禁止 handler/adapter 直接赋字符串。

| 当前 | 事件 | 下一状态 | 事务内副作用 |
| --- | --- | --- | --- |
| pending/awaiting_upload | `UPLOAD_CONFIRMED` | pending/queued | 保存 ETag、uploaded_at、queued_at。 |
| pending/awaiting_upload | `USER_CANCEL` | cancelled/done | 标记对象清理 pending。 |
| pending/queued | `CLAIM` | running/downloading | 写 lease、heartbeat、started_at。 |
| pending/queued | `DEVICE_NOT_READY` | pending/waiting_device | 写错误、next_attempt_at，不占运行槽。 |
| pending/waiting_device | `DEVICE_READY` | pending/queued | 清错误并重新排队。 |
| running/downloading | `DOWNLOAD_OK` | running/staging_device | 写 SHA-256。 |
| running/staging_device | `MEDIA_VISIBLE` | running/dispatching_artemis | 写 device_path、device cleanup pending。 |
| running/dispatching_artemis | `ATTEMPT_CREATED` | running/dispatching_artemis | insert attempt，生成 session ID。 |
| running/dispatching_artemis | `ARTEMIS_ADMITTED` | running/waiting_artemis | attempt queued/running。 |
| running/waiting_artemis | `ARTEMIS_SUCCESS` | succeeded/done | attempt success；同事务写 cleanup intent，设备清理由 selector 领取。 |
| running/waiting_artemis | `SAFE_FAILURE_RETRY` | pending/queued | attempt failed，释放 lease，设置退避。 |
| running/waiting_artemis | `AMBIGUOUS_FAILURE` | running/verifying | attempt unknown，创建 verify。 |
| running/verifying | `FOUND_PUBLISHED` | succeeded/done | verify success；同事务写 cleanup intent。 |
| running/verifying | `CONFIRMED_ABSENT` | needs_review/done | verify success；释放 lease但不重试 publish，只允许用户之后显式再次核验。 |
| running/verifying | `INCONCLUSIVE` | needs_review/done | 释放 lease，保留对象。 |
| 任意 pending | `USER_CANCEL` | cancelled/done | 清理对象；运行态不允许普通取消。 |
| failed | `USER_RETRY` | pending/queued | 校验对象/预算，清主表错误和 completed_at。 |
| needs_review | `USER_VERIFY` | running/verifying | 在同一事务取得全局 PostgreSQL advisory/device slot，确认不存在其他 running task，也不存在全局 pending/failed/leased device cleanup 后创建 verify attempt；API 不伪造 Worker lease，由真实 Worker 随后领取。 |

额外不变量：

- Worker 已领取的 running 执行必须同时匹配 `lease_owner/lease_expires_at/row_version`；刚由 API 创建、等待真实 Worker 领取的 `running/verifying` 可暂时无 publish lease；非 running 必须清空 publish lease；
- `stage=waiting_artemis/verifying` 必须能解析出一条活跃 attempt；
- `kind=verify` 必须有 `related_attempt_id`；
- `status=succeeded` 后禁止创建新的 publish attempt；
- `row_version` 不匹配时返回并发冲突，调用方重新读取，不覆盖较新的状态；
- 状态转换、attempt insert、计数和审计字段在同一 DB 事务中提交。
## 20. 后端核心逻辑

### 20.1 实际 service / handler owner

- `publishing.submission.CreateCommand` 与 `create_upload_ticket()` 创建/幂等重放上传票据；
- `publishing.submission.confirm_upload()`、`cancel_task()`、`retry_task()`、`replace_upload()` 持有对应任务写入；
- `api.v2.video_publish.refresh_upload_url()` 只负责 owner 校验和 wire envelope；`submission.refresh_upload_ticket()` 在返回 URL 前以 PostgreSQL 时间持久化该 generation 的 ticket expiry；
- `api.v2.video_publish.current()`、`list_tasks()`、`detail()` 持有 owner-scoped 只读查询与 snapshot；
- `publishing.repository.request_verification()` 与 device cleanup selector 使用同一事务 advisory/device slot：verification 在创建 attempt 前检查全局 running/cleanup，device selector 在领取 lease 前检查全局 running；持久化的 running 状态与 pending/failed cleanup 状态让 advisory lock 释放后的物理设备操作继续互斥；Worker 侧 task/attempt 结果只由 `commit_publish_transition()` 原子提交；
- `publishing.dispatcher.dispatch_one()` 与 `recover_active()` 编排 adapter，但 adapter 不得自行修改数据库状态；
- cleanup 删除只由 `claim_cleanup_work()` 领取的 executor 执行，并由 `renew_cleanup_work()` / `finish_cleanup_work()` fence。

FastAPI 只做 Pydantic wire 校验、鉴权、owner scope 和错误映射，不虚构独立 service 名称。

### 20.2 创建上传任务

```python
def create_upload_ticket(session, cmd):
    validate_filename_caption_size(cmd)
    existing = select_by_client_request_id(cmd.client_request_id)
    if existing:
        require_same_actor(existing, cmd.actor_user_id)
        assert_same_create_payload(existing, cmd)
        return ticket_for_existing_or_refresh(existing, replay=True)

    task_id = uuid4()
    key = build_server_owned_object_key(task_id, cmd.filename)
    row = VideoPublishTask(
        public_id=task_id,
        client_request_id=cmd.client_request_id,
        caption=normalize_caption(cmd.caption),
        object_bucket=config.video_bucket,
        object_key=key,
        status="pending",
        stage="awaiting_upload",
        target_device_serial=config.device_serial,
        target_app_package=config.app_package,
        ...
    )
    session.add(row)
    session.commit()
    return presign_after_commit(row)
```

若 presign 在 commit 后失败，任务保留为 awaiting_upload，客户端可调用 refresh upload URL；不得回滚已经返回/可能重试的幂等键。

### 20.3 确认上传

```python
def confirm_upload(session, task_id, actor):
    row = select_for_update(task_id)
    require_actor_access(row, actor)
    require_stage(row, "awaiting_upload")
    stat = object_store.stat(row.object_bucket, row.object_key)  # 锁外调用更优：先读快照，再 CAS
    validate_stat(stat, expected_size=row.size_bytes, expected_type=row.content_type)
    update_where_version(
        task_id, row.row_version,
        object_etag=stat.etag,
        object_uploaded_at=db_now(),
        status="pending", stage="queued", queued_at=db_now(),
    )
    commit()
```

远程 HEAD 不应在持有长事务时执行。推荐“两段式”：短事务读取快照 → HEAD → 短事务 `SELECT FOR UPDATE`/version 检查并落状态；如果期间任务已取消则删除对象并返回 409。

### 20.4 Worker 主循环

```python
async def run_forever():
    heartbeat.start(interval=5)
    await recover_active()
    while not stopping:
        await cleanup_due_resources(limit=10)
        outcome = await dispatch_one()
        if outcome.kind == "no_task":
            await wake_event.wait(timeout=2)
        else:
            await asyncio.sleep(0)
```

SIGTERM：停止领取新任务、写 heartbeat=stopping、取消本地等待但不调用 Artemis stop；已经提交的 Artemis session 由下一实例按 ID 恢复查询。

### 20.5 原子领取

```python
def claim_one(session, instance_id):
    row = session.execute(
        select(VideoPublishTask)
        .where(status == "pending", stage.in_(["queued", "waiting_device"]))
        .where(next_attempt_at.is_(None) | (next_attempt_at <= func.now()))
        .order_by(queued_at, id)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).scalar_one_or_none()
    if not row:
        return None

    # waiting_device 先做便宜 readiness；不可用则保持 pending 并退避。
    transition(row, event="CLAIM", owner=instance_id, lease_seconds=30)
    session.commit()  # 此后不持有事务
    return immutable_snapshot(row)
```

部分唯一索引冲突表示另一个 Worker 已获得全局槽；捕获 `IntegrityError`、rollback，并返回 no_task，不把它记成业务失败。

### 20.6 执行管线

```python
async def execute_claimed(task):
    try:
        await heartbeat_task(task)
        local_path = spool_path_for(task.public_id)
        transition_short_tx(
            task, "DOWNLOAD_START", spool_path=str(local_path),
            spool_cleanup_status="pending"
        )  # 在任何下载副作用前登记 owner；文件尚不存在时清理也必须幂等成功
        local_path = await object_store.download_atomic(task.object_ref, local_path)
        verify_size_and_sha256(local_path)
        transition_short_tx(task, "DOWNLOAD_OK", sha256=...)

        await adb.check_device(task.device_serial)
        await adb.check_package(task.device_serial, task.app_package)
        await adb.ensure_album_empty(task.device_serial)
        transition_short_tx(
            task, "DEVICE_STAGE_START", device_path=...,
            device_cleanup_status="pending"
        )
        await adb.stage_video(local_path, task.device_path)
        await adb.verify_media_visible(task.device_serial, task.device_path)
        transition_short_tx(task, "MEDIA_VISIBLE")

        attempt = create_publish_attempt_short_tx(task)
        await submit_idempotently(attempt)
        result = await poll_artemis(attempt)
        await apply_artemis_result(task, attempt, result)
    except SafePreArtemisFailure as exc:
        await schedule_retry_or_fail(task, exc)
    except BaseException as exc:
        await persist_unexpected_failure_without_losing_session(task, exc)
```

每个外部调用前后都以短事务写 stage/heartbeat。网络和 ADB 调用期间不得占用数据库连接。attempt 已创建后的未预期异常必须在同一 fenced 事务把该 attempt/session 标为 `unknown`、释放 task lease 但保持 task 为可恢复的 `running/waiting_artemis|verifying`；下一 Worker 只查询同一 session，不能只终止 task 或创建新 attempt。执行协程不直接删除已登记 spool；设备、spool、MinIO 只由 cleanup selector 领取独立租约后执行。

### 20.7 创建并提交 Artemis attempt

```python
def create_attempt(session, task, kind, related=None):
    lock_task_for_update(task.id)
    assert_no_active_attempt(task.id)
    seq = max_sequence(task.id) + 1
    session_id = uuid4()
    prompt = render_versioned_prompt(task, kind, session_id)
    attempt = Attempt(
        task_id=task.id,
        sequence_no=seq,
        kind=kind,
        related_attempt_id=related,
        artemis_session_id=session_id,
        status="created",
        prompt_version=current_prompt_version(kind),
        prompt_snapshot=prompt,
        device_serial=task.target_device_serial,
    )
    session.add(attempt)
    if kind == "publish":
        task.attempt_count += 1
    transition(task, "ATTEMPT_CREATED")
    session.commit()
    return attempt
```

提交：

```python
async def submit_idempotently(attempt):
    mark_attempt(attempt, "submitting")
    try:
        handle = await artemis.submit(
            goal=attempt.prompt_snapshot,
            task_id=str(attempt.artemis_session_id),
            device_serial=attempt.device_serial,
            profile=attempt.artemis_profile,
            locked_app_package=attempt.target_app_package,
            verification_level=attempt.artemis_verification_level,
        )
    except TransportError:
        # 先 GET 同一 ID；仍未知时才用相同 ID 重提。
        handle = await query_then_resubmit_same_id(attempt)
    mark_admitted(attempt, handle.status)
```

绝对禁止在 transport timeout 后生成新 session ID。

### 20.8 Artemis 轮询

```python
async def poll_artemis(attempt):
    deadline = monotonic() + config.poll_window_seconds
    while monotonic() < deadline:
        result = await artemis.get_task(attempt.session_id)
        persist_poll_snapshot(attempt, result)
        if result.done:
            return result
        await sleep(config.poll_interval_seconds)
    return PollWindowElapsed()  # 非 terminal，Worker 下一轮继续同一 attempt
```

`PollWindowElapsed` 不把 task 改成 failed。Worker 释放本轮协程后，任务仍 running/waiting_artemis，由 recovery/下一轮继续查询。

### 20.9 失败分类与核验

分类器输入只能是稳定事实：Artemis status、steps 数量、最后步骤类型、是否观察到最终发布动作、设备/网络阶段。不要只匹配自由文本错误。

```python
classification = classify_failure(attempt, session, steps)
if classification.retry_safe is True:
    schedule_publish_retry()
elif classification.code in {"publish_action_observed", "session_missing"}:
    create_verify_attempt(related_attempt=attempt.id)
else:
    transition_to_needs_review()
```

verify 结果必须解析为受控枚举，不接收任意自然语言作为状态：

```json
{
  "verdict": "published | not_published | inconclusive",
  "evidence": "bounded plain text",
  "observedCaption": "optional",
  "observedAt": "optional ISO timestamp"
}
```

### 20.10 清理逻辑

清理操作逐项幂等：

```python
async def cleanup_task(work):
    # work 由单条 CTE / UPDATE ... RETURNING 原子领取，包含明确 due resources。
    await cleanup_one("device", lambda: adb.remove(work.device_path))
    await cleanup_one("spool", lambda: unlink_exact_final_and_part(work.spool_path))
    await cleanup_one(
        "object",
        lambda: object_store.remove(work.object_key, work.object_etag),
    )  # work 绑定退休 generation；PUT expiry 到期前不得领取
    finish_cleanup_work(work.token, resource_results)  # owner/expiry/version CAS
```

失败/needs_review 的 MinIO 对象默认保留。awaiting_upload 超过 24 小时自动取消并删除；failed 对象按 `TIKTOK_PUBLISH_FAILED_RETENTION_DAYS` 保留，过期删除前仍保留任务和 attempt 审计记录。

### 20.11 Repository 与事务规则

- PostgreSQL 使用既有 SQLAlchemy 2 sync session；异步 Worker 用线程边界或短同步函数，不跨 `await` 持有 Session；
- publish observation 只通过 `commit_publish_transition()` 提交，task 与 attempt 在同一 owner/DB-expiry/rowVersion CAS 事务中完成；自动 verify 也在该事务创建；
- cleanup 只通过 `claim_cleanup_work()` 原子领取显式 due resources，并由 `finish_cleanup_work()` 在同一 owner/DB-expiry/rowVersion fence 下写结果；
- repository 方法不自行吞异常；domain service 决定 rollback、错误码和状态；
- 所有租约、deadline、retry、完成和 retention 时间判断使用单次读取的 PostgreSQL `clock_timestamp()`，避免多主机时钟漂移；
- 列表查询使用 lateral/subquery 取最新 attempt，避免 N+1；
- caption、Prompt、output 不进入普通应用日志；
- attempt/history 永不物理覆盖；只追加新执行记录并更新当前状态字段。
## 21. 完整 HTTP 契约示例

### 21.1 通用响应和错误

成功响应直接返回业务对象，不再包多层 `data`。错误使用：

```json
{
  "detail": {
    "code": "UPLOAD_SIZE_MISMATCH",
    "message": "视频实际大小与创建任务时不一致，请重新上传。",
    "retryable": true,
    "allowedActions": ["continue_upload", "cancel"],
    "requestId": "req_01J..."
  }
}
```

| HTTP | 使用场景 |
| --- | --- |
| 400 | JSON/参数组合非法。 |
| 401 | 未登录。 |
| 403 | 角色或页面权限不足。 |
| 404 | task 不存在或用户不可见；不泄漏其他用户任务。 |
| 409 | 状态冲突、版本冲突、动作当前不允许。 |
| 413 | 视频声明大小超过上限。 |
| 422 | 文件/文案业务校验失败。 |
| 503 | MinIO、Worker、Artemis 或目标设备当前不可用。 |

### 21.2 配置

```http
GET /v2/video-publish/config
```

```json
{
  "acceptedContentTypes": ["video/mp4"],
  "acceptedExtensions": [".mp4"],
  "maxVideoBytes": 524288000,
  "maxCaptionCharacters": 4000,
  "uploadUrlTtlSeconds": 900,
  "target": {
    "appName": "TikTok",
    "appPackage": "com.zhiliaoapp.musically",
    "deviceSerialMasked": "D123…00AC",
    "album": "TTSERP"
  },
  "worker": {
    "status": "ready",
    "lastHeartbeatAt": "2026-10-04T10:42:14Z"
  },
  "device": {
    "status": "ready",
    "message": "设备在线、已解锁且当前空闲"
  },
  "canWrite": true,
  "serverTime": "2026-10-04T10:42:15Z"
}
```

### 21.3 创建任务

```http
POST /v2/video-publish/tasks
Content-Type: application/json
X-Requested-With: tts-erp
```

```json
{
  "clientRequestId": "f87e2d3b-b746-42a8-a45e-33e8d63ef126",
  "filename": "launch-video.mp4",
  "contentType": "video/mp4",
  "sizeBytes": 15393429,
  "caption": "新品已经上线 🎉\n#new #tiktok"
}
```

首次创建返回 201；相同幂等键和相同 payload 返回 200：

```json
{
  "taskId": "38f1b046-9c20-47ee-a576-51431811d345",
  "idempotentReplay": false,
  "status": "pending",
  "stage": "awaiting_upload",
  "rowVersion": 1,
  "upload": {
    "method": "PUT",
    "url": "https://minio.example/...signed...",
    "headers": {"Content-Type": "video/mp4"},
    "expiresAt": "2026-10-04T10:57:15Z"
  },
  "allowedActions": ["continue_upload", "cancel"]
}
```

相同 `clientRequestId` 但 payload 不同返回 409 `IDEMPOTENCY_PAYLOAD_MISMATCH`。

可选字段 `deviceSerial`（string，缺省不发送）指定目标设备。缺省或 `null` 时回退到配置的 `ARTEMIS_DEVICE_SERIAL`；显式提供时必须匹配 `^[A-Za-z0-9._:-]{1,64}$`，空串或非法字符返回 422 `DEVICE_SERIAL_INVALID`。创建时不探测设备在线状态——未知/离线设备由 Artemis 在提交阶段拒绝（§411），offline/locked/busy 仍允许排队。页面从 §21.15 的设备列表选择后随创建请求发送；列表接口不可用时前端省略该字段并回退配置设备。相同幂等键重放以已存在任务为准（重放不校验 `deviceSerial`，响应中的 `target.deviceSerialMasked` 是任务实际设备的快照）。

### 21.4 浏览器 PUT MinIO

```http
PUT <upload.url>
Content-Type: video/mp4
Content-Length: 15393429

<binary>
```

成功为 200/204。浏览器不解析响应 XML，只记录 HTTP status 和 ETag header（若 CORS 暴露）；最终以 confirm 的服务端 HEAD 为准。

### 21.5 刷新上传 URL

```http
POST /v2/video-publish/tasks/38f1.../upload-url
X-Requested-With: tts-erp
```

请求 body 为空。返回新的 `upload` 对象和原 task snapshot。只有 awaiting_upload 可调用。

### 21.6 Confirm

```http
POST /v2/video-publish/tasks/38f1.../confirm-upload
Content-Type: application/json
X-Requested-With: tts-erp

{"rowVersion": 1}
```

```json
{
  "taskId": "38f1b046-9c20-47ee-a576-51431811d345",
  "status": "pending",
  "stage": "queued",
  "queuePosition": 3,
  "queuedAt": "2026-10-04T10:45:00Z",
  "rowVersion": 2,
  "latestArtemisSessionId": null,
  "allowedActions": ["view", "cancel"]
}
```

### 21.7 当前任务

```http
GET /v2/video-publish/tasks/current
If-None-Match: "publish-current-a81f"
```

```json
{
  "task": {
    "taskId": "38f1b046-9c20-47ee-a576-51431811d345",
    "filename": "launch-video.mp4",
    "status": "running",
    "statusLabel": "执行中",
    "stage": "waiting_artemis",
    "stageLabel": "等待 Artemis",
    "currentAttempt": {
      "sequenceNo": 1,
      "kind": "publish",
      "kindLabel": "正式发布",
      "artemisSessionId": "c8af5a6c-4158-49c3-9141-13bc69b391d2",
      "status": "running",
      "startedAt": "2026-10-04T10:46:10Z"
    },
    "stageStartedAt": "2026-10-04T10:46:10Z",
    "updatedAt": "2026-10-04T10:46:14Z"
  },
  "suggestedPollSeconds": 2,
  "serverTime": "2026-10-04T10:46:15Z"
}
```

无当前任务返回 200 `{ "task": null, "suggestedPollSeconds": 30, ... }`，不是 404。

### 21.8 列表

```http
GET /v2/video-publish/tasks?status=failed&limit=30&cursor=eyJpZCI6MTAwNX0
```

```json
{
  "items": [
    {
      "taskId": "38f1b046-9c20-47ee-a576-51431811d345",
      "filename": "launch-video.mp4",
      "sizeBytes": 15393429,
      "captionPreview": "新品已经上线 🎉",
      "status": "failed",
      "statusLabel": "失败",
      "stage": "done",
      "stageLabel": "执行结束",
      "publishAttemptCount": 2,
      "verifyAttemptCount": 1,
      "latestArtemisSessionId": "c8af5a6c-4158-49c3-9141-13bc69b391d2",
      "lastErrorCode": "PLANNER_ZERO_STEPS",
      "lastErrorMessage": "Artemis Planner 未执行设备步骤。",
      "createdBy": "user:42",
      "createdAt": "2026-10-04T10:40:00Z",
      "updatedAt": "2026-10-04T10:50:00Z",
      "allowedActions": ["view", "retry", "copy_artemis_id"]
    }
  ],
  "nextCursor": null,
  "totalApprox": 1,
  "serverTime": "2026-10-04T10:51:00Z"
}
```

排序固定为 `created_at DESC, id DESC`；cursor 编码排序键，不使用大 offset。

### 21.9 详情

```http
GET /v2/video-publish/tasks/38f1b046-9c20-47ee-a576-51431811d345
```

```json
{
  "taskId": "38f1b046-9c20-47ee-a576-51431811d345",
  "filename": "launch-video.mp4",
  "contentType": "video/mp4",
  "sizeBytes": 15393429,
  "caption": "新品已经上线 🎉\n#new #tiktok",
  "status": "failed",
  "stage": "done",
  "target": {
    "appName": "TikTok",
    "appPackage": "com.zhiliaoapp.musically",
    "deviceSerialMasked": "D123…00AC",
    "album": "TTSERP"
  },
  "object": {
    "bucket": "tiktok-video",
    "key": "video-publish/2026/10/38f1.../launch-video.mp4",
    "etag": "8d0315ee953709c05cf91e8866b41440",
    "deletedAt": null
  },
  "attempts": [
    {
      "sequenceNo": 1,
      "kind": "publish",
      "kindLabel": "正式发布",
      "artemisSessionId": "c8af5a6c-4158-49c3-9141-13bc69b391d2",
      "status": "failed",
      "stepsCount": 0,
      "retryClassification": "planner_zero_steps",
      "retrySafe": true,
      "error": "Planner timed out before device execution.",
      "startedAt": "2026-10-04T10:46:10Z",
      "finishedAt": "2026-10-04T10:52:10Z"
    }
  ],
  "cleanup": {
    "device": {"status": "succeeded", "error": null},
    "spool": {"status": "succeeded", "error": null},
    "object": {"status": "not_started", "error": null}
  },
  "allowedActions": ["view", "retry", "copy_artemis_id"],
  "rowVersion": 9,
  "updatedAt": "2026-10-04T10:52:11Z"
}
```

非 admin 不返回 `promptSnapshot` 和未裁剪 `artemisOutput`；admin 可用显式 `?includeDiagnostics=true` 获取。

### 21.10 Cancel

```http
POST /v2/video-publish/tasks/{taskId}/cancel
Content-Type: application/json
X-Requested-With: tts-erp

{"rowVersion": 2}
```

成功返回最新 snapshot。不允许取消的阶段返回结构化 409 `TASK_ACTION_NOT_ALLOWED`。

### 21.11 Retry

```http
POST /v2/video-publish/tasks/{taskId}/retry
Content-Type: application/json
X-Requested-With: tts-erp

{"rowVersion": 9}
```

```json
{
  "taskId": "38f1...",
  "status": "pending",
  "stage": "queued",
  "queuePosition": 2,
  "queuedAt": "2026-10-04T10:55:00Z",
  "publishAttemptCount": 2,
  "latestArtemisSessionId": "c8af5a6c-4158-49c3-9141-13bc69b391d2",
  "allowedActions": ["view", "cancel"],
  "rowVersion": 10
}
```

重试入队时尚未生成下一条 Artemis session，因此 latest 仍是上一条；新 attempt 创建后定时刷新会显示新 ID。

### 21.12 Verify

```http
POST /v2/video-publish/tasks/{taskId}/verify
Content-Type: application/json
X-Requested-With: tts-erp

{"rowVersion": 12}
```

只允许 needs_review。返回 `running/verifying` 和新建 verify attempt 的 Artemis ID；前端立即显示该 ID。

### 21.13 Retry cleanup

```http
POST /v2/video-publish/tasks/{taskId}/cleanup/retry
Content-Type: application/json
X-Requested-With: tts-erp

{"resources": ["device", "spool", "object"], "rowVersion": 14}
```

只重试当前为 failed 的资源；请求 succeeded/not_started 资源不会再次产生破坏性动作。

### 21.14 稳定错误码

| code | HTTP | 可重试 | 前端动作 |
| --- | --- | --- | --- |
| `INVALID_VIDEO_TYPE` | 422 | 是 | 重新选择 MP4。 |
| `VIDEO_TOO_LARGE` | 413 | 是 | 重新选择较小文件。 |
| `CAPTION_REQUIRED` | 422 | 是 | 聚焦文案框。 |
| `CAPTION_TOO_LONG` | 422 | 是 | 展示上限并聚焦文案框。 |
| `DEVICE_SERIAL_INVALID` | 422 | 否 | 重新选择设备。 |
| `IDEMPOTENCY_PAYLOAD_MISMATCH` | 409 | 否 | 生成新的 clientRequestId 后重新创建。 |
| `UPLOAD_NOT_FOUND` | 422 | 是 | 重试上传。 |
| `UPLOAD_SIZE_MISMATCH` | 422 | 是 | 重试上传。 |
| `UPLOAD_MIME_MISSING` / `UPLOAD_MIME_MISMATCH` | 422 | 是 | 重新上传有效 MP4。 |
| `UPLOAD_ETAG_MISSING` | 422 | 是 | 保持 awaiting_upload，重试上传/确认。 |
| `UPLOAD_URL_EXPIRED` | 409 | 是 | 请求新 upload URL。 |
| `TASK_ACTION_NOT_ALLOWED` | 409 | 取决于状态 | 按 allowedActions 重绘。 |
| `TASK_VERSION_CONFLICT` | 409 | 是 | 重新 GET，再让用户确认动作。 |
| `PUBLISH_WORKER_UNAVAILABLE` | 503 | 是 | 保留任务，提示等待恢复。 |
| `DEVICE_OFFLINE` | 503 | 是 | 自动 waiting_device。 |
| `DEVICE_LOCKED` | 503 | 是 | 自动 waiting_device，提示解锁。 |
| `APP_NOT_INSTALLED` | 503 | 否 | 运维安装 TikTok。 |
| `MEDIA_SCAN_FAILED` | 503 | 是 | 自动退避，保留 MinIO。 |
| `ARTEMIS_REJECTED` | 503 | 取决于原因 | 展示拒绝原因。 |
| `ARTEMIS_UNREACHABLE` | 503 | 是 | 同 session 重查/重提。 |
| `ARTEMIS_UNKNOWN_OUTCOME` | 409 | 否 | 自动 verify，不显示直接重试。 |
| `VERIFY_INCONCLUSIVE` | 409 | 否 | needs_review，可再次自动核验。 |
| `RETRY_BUDGET_EXHAUSTED` | 409 | 否 | 查看历史，不再自动重试。 |
| `CLEANUP_FAILED` | 503 | 是 | 显示重试清理。 |

### 21.15 设备列表

```http
GET /v2/video-publish/devices
```

代理 Artemis `GET /api/devices`，供页面设备选择器展示当前可选设备：

```json
{
  "devices": [
    {
      "serial": "D123084100AC",
      "serialMasked": "D123…00AC",
      "model": "NX712J",
      "product": "CN_PQ82A11",
      "state": "device",
      "isBusy": false,
      "isEmulator": false
    }
  ]
}
```

- 选择面返回精确 `serial`：创建请求必须回传它（§21.3），且该端点与 `/config` 同属 `page:video-publish` 已授权面；任务列表/详情/指标继续只返回掩码。不透传 `active_task_desc`、`active_session_id` 等可能含文案或会话信息的字段。
- Artemis 不可达或未配置 `ARTEMIS_BASE_URL` 时返回 503 `ARTEMIS_UNREACHABLE`（可重试）；前端此时隐藏选择器并回退配置设备。
- 设备 offline/locked/busy 不阻止展示与创建排队任务。
## 22. 前端实现规格

### 22.1 页面组件树

```text
VideoPublishPage
├── PageHeader
│   ├── TitleAndDescription
│   ├── DeviceReadiness
│   └── RefreshControls
├── ActivePublishRail
│   ├── StageTrack
│   ├── ActiveTaskSummary
│   └── ActiveArtemisIdCopy
├── SubmissionWorkbench
│   ├── VideoPickerAndPreview
│   ├── CaptionEditor
│   ├── DevicePicker
│   ├── DispatchSummary
│   └── SubmitFooter
├── TaskHistory
│   ├── StatusFilters
│   ├── RefreshStatus
│   ├── TaskTableOrMobileCards
│   └── CursorPagination
├── TaskDetailDrawer
│   ├── ContentSection
│   ├── ProgressSection
│   ├── AttemptTimeline
│   ├── CleanupSection
│   └── StickyActionFooter
└── Dialogs
    ├── SubmitConfirmDialog
    ├── CancelConfirmDialog
    ├── RetryConfirmDialog
    └── VerifyConfirmDialog
```

不引入 React/Vue。沿用项目的服务端模板 + 页面级原生 JavaScript。复杂度收拢到一个页面 controller，不把每个按钮拆成浅层 module。

### 22.2 HTML 元素和稳定选择器

| 元素 | ID/data 属性 | 用途 |
| --- | --- | --- |
| 页面 notice | `#publish-notice` | 全局非阻塞提示，`aria-live=polite`。 |
| 设备状态 | `#publish-device-status` | ready/busy/offline/locked/unknown。 |
| 设备选择 | `#publish-device-select` | select：首项为配置默认设备，其余来自 §21.15 实时列表；列表不可用时仅剩默认项。 |
| 刷新模式 | `#publish-refresh-mode` | select：smart/2/5/10/30/off。 |
| 上次刷新 | `#publish-last-refreshed` | 服务端时间和本地展示。 |
| 立即刷新 | `#publish-refresh-now` | 手动执行 current/list/detail refresh。 |
| 轨道 | `#publish-rail` | 当前任务阶段。 |
| 当前任务 ID | `#active-task-id` | ttsERP UUID。 |
| 当前 Artemis ID | `#active-artemis-id` | 完整 UUID。 |
| 复制当前 ID | `[data-copy-artemis-id]` | 复制对应完整 UUID。 |
| 文件 input | `#publish-video-file` | `accept=video/mp4,.mp4`。 |
| drop zone | `#publish-video-drop` | 点击转发到 input；支持 drag/drop。 |
| 本地预览 | `#publish-video-preview` | muted、controls、playsinline。 |
| 文案 | `#publish-caption` | textarea；不富文本。 |
| 字符计数 | `#publish-caption-count` | `aria-live=polite`，不要每个字符都朗读，可 debounce。 |
| 提交按钮 | `#publish-submit` | 上传并加入发布队列。 |
| 上传进度 | `#publish-upload-progress` | progressbar。 |
| 取消上传 | `#publish-upload-cancel` | 只在 XHR active 时出现。 |
| 状态 tabs | `[data-task-filter]` | 全部/排队/执行中/失败/需核验/成功/取消。 |
| 列表 body | `#publish-task-list` | desktop table/mobile cards 的共同数据源。 |
| drawer | `#publish-task-drawer` | 详情容器。 |
| drawer 关闭 | `#publish-drawer-close` | 恢复原行焦点。 |
| drawer actions | `#publish-drawer-actions` | sticky footer，按 allowedActions 渲染。 |
| dialog | `#publish-confirm-*` | 四类确认框。 |

自动化测试可以依赖这些 ID/data 属性；CSS 不应依赖测试专用类名。

### 22.3 尺寸与布局

Desktop ≥ 1200px：

- `.page-main` 最大宽度 1440px；
- SubmissionWorkbench 使用 `grid-template-columns: minmax(0, 7fr) minmax(300px, 3fr)`；
- 文件预览和文案在左栏上下排列，投递摘要和主按钮在右栏；
- TaskHistory 占满整行；
- drawer 宽度 `min(560px, 42vw)`，从右侧覆盖，不改变表格列宽。

Tablet 720–1199px：

- workbench 改为 `6fr/4fr`；
- 列表隐藏创建人列，时间显示相对时间并保留 title 完整时间；
- Artemis ID 列可视觉显示前 8 + 后 5 位，但复制完整 UUID。

Mobile < 720px：

- 全部单列；
- 主按钮固定在表单内容底部，不做遮挡内容的 viewport fixed；
- table 转 cards；
- drawer 为全屏；
- refresh controls 分两行；
- 按钮点击区域至少 44×44px。

### 22.4 页头按钮位置

页头左侧：eyebrow、标题、说明。页头右侧从上到下：

1. 设备状态：`设备 D123…00AC · 在线/忙碌/离线`；
2. Worker 状态：`发布服务正常` 或 `发布服务心跳已中断`；
3. 刷新控制：模式 select + `立即刷新` 按钮；
4. 小字：`上次刷新 10:42:15`。

`立即刷新` 使用 `.btn-secondary`，刷新中 disabled 并显示 `刷新中…`。它只读，不需要二次确认。

### 22.5 新建任务区域按钮位置

文件区：

- drop zone 中央主文案 `选择 MP4 视频`；
- 次文案显示大小上限；
- 选中后，预览右上角放 `重新选择` 次按钮；
- 不提供“删除本地文件”含混文案，使用 `清除选择`。

文案区：

- label 左侧 `发布文案`；右侧字符计数；
- textarea 下方仅显示校验/保留规则，不放提交按钮。

投递摘要右栏：

- 只读显示 App、设备、相册、文件、文案字符数、预计队列位置；
- 底部 footer 右对齐主按钮 `上传并加入发布队列`；
- 主按钮下方小字：`加入队列后不能修改视频和文案`；
- 上传时 footer 变为进度条 + 左侧 `取消上传` + 右侧 `上传中 42%`，不再显示第二个可点击提交按钮。

主按钮启用条件：

```text
config loaded
AND canWrite
AND file valid
AND caption valid
AND no upload in progress
AND clientRequestId flow not active
```

设备离线不禁用创建按钮：任务可以排队等待设备；Worker unavailable 时禁用并说明原因，避免创建永远无人处理的任务。

### 22.6 列表按钮位置

桌面表格最后一列固定为“操作”：

- 每行最多显示一个 primary/secondary action + `查看`；
- failed 可重试：`重试` + `查看`；
- needs_review：`自动核验` + `查看`；
- queued：`取消` + `查看`；
- running/succeeded：只显示 `查看`；
- cleanup failed：`重试清理` + `查看`。

Artemis ID 位于独立列，不放在操作列：

```text
c8af5a6c…391d2  [复制]
```

`复制` 是图标+可见文字的小按钮，成功后原位变为 `已复制` 1.5 秒，失败则 notice 显示“复制失败，请手动选择完整 ID”。使用 `navigator.clipboard.writeText(fullId)`，无权限时回退到临时 textarea + `document.execCommand('copy')`。

移动卡片顺序：状态 → 文件 → Artemis ID/复制 → 时间 → 主操作。不得把 Artemis ID 藏到详情里才可见。

### 22.7 Drawer 按钮位置

Drawer header：

- 左：`任务 #<短 UUID>` + 状态 badge；
- 右：`复制任务 ID`、关闭按钮。

Attempt timeline 每一项右上：`复制 Artemis ID`；ID 正文完整换行显示，使用 `overflow-wrap:anywhere`。

Drawer sticky footer：

- 左侧危险/次操作：`取消任务` 或空；
- 右侧主操作：`重试原任务`、`再次自动核验` 或 `重试清理`；
- `查看` 本身不出现在 drawer footer；
- footer 按 allowedActions 重绘，动作提交中全部按钮 disabled，避免重复点击。

### 22.8 Dialog 逐项规格

**提交确认**：

- title：`确认加入发布队列`；
- body：视频名/大小、本地静音缩略预览、完整文案、App、设备、相册；
- warning：`设备空闲时任务可能立即开始，入队后不能修改视频和文案。`；
- 左下 `返回修改`，右下 primary `确认并上传`。

**取消确认**：

- title：`取消这个发布任务？`；
- body 说明是否会删除已上传 MinIO 视频；
- 左 `保留任务`，右 danger `取消任务`；
- running 时不渲染此 dialog 入口。

**重试确认**：

- title：`重试原任务`；
- 展示上一 attempt 的完整 Artemis ID、失败阶段、错误、当前正式发布次数/上限；
- warning：`系统已确认上一次没有完成发布。重试会创建新的 Artemis ID。`；
- 左 `暂不重试`，右 primary `加入重试队列`。

**自动核验确认**：

- title：`再次自动核验`；
- 展示待核验 publish attempt 的 Artemis ID；
- body：`只查看作品页和草稿箱，不会发布内容。`；
- 左 `取消`，右 primary `开始核验`。

所有 dialog 关闭后恢复触发按钮焦点；Escape 等同取消，不触发写请求。

### 22.9 前端状态对象

```javascript
const state = {
  config: null,
  form: {
    file: null,
    objectUrl: null,
    caption: '',
    clientRequestId: null,
    taskId: null,
    phase: 'empty',
    uploadProgress: 0,
  },
  current: null,
  tasks: [],
  nextCursor: null,
  filter: 'all',
  selectedTaskId: null,
  selectedTask: null,
  refresh: {
    mode: 'smart',
    timer: null,
    generation: 0,
    currentEtag: null,
    listEtag: null,
    detailEtag: null,
    failureCount: 0,
    lastSuccessAt: null,
  },
};
```

所有状态写入集中到 controller 函数；事件 handler 不直接散改多个 DOM 节点。推荐函数：

```text
loadConfig
validateForm
openSubmitDialog
createAndUpload
confirmUpload
refreshCurrent
refreshList
refreshDetail
scheduleNextRefresh
renderRail
renderTaskList
renderDrawer
performTaskAction
copyArtemisId
```

### 22.10 状态 badge 文案

| API 值 | UI 文案 | 颜色 |
| --- | --- | --- |
| pending/awaiting_upload | 等待上传 | muted |
| pending/queued | 排队中 | warn |
| pending/waiting_device | 等待设备 | warn |
| running/downloading | 下载视频 | accent |
| running/staging_device | 写入手机 | accent |
| running/dispatching_artemis | 提交 Artemis | accent |
| running/waiting_artemis | 手机执行中 | accent |
| running/verifying | 自动核验中 | accent |
| terminal/done + `operationalStage=cleaning` | 清理中 | accent |
| succeeded/done | 已发布 | ok |
| failed/done | 失败 | danger |
| needs_review/done | 需确认 | warn |
| cancelled/done | 已取消 | muted |

颜色只是辅助，badge 必须含文字。

### 22.11 日期、时长与 UUID 格式

- 绝对时间：`YYYY-MM-DD HH:mm:ss`，浏览器本地时区；title 放 UTC 原值；
- 5 分钟内可附加相对时间，但不能只显示“刚刚”；
- 时长：`4m 19s` 或中文 `4分19秒`，全页统一；
- ttsERP task ID 与 Artemis ID 标签必须明确，不能都写“任务 ID”；
- UUID copy 使用完整原始值；视觉缩写固定为前 8、后 5：`c8af5a6c…391d2`。
## 24. 日志、监控与运维

### 24.1 结构化日志

每条 Worker 日志至少包含：

```json
{
  "event": "artemis_poll",
  "task_id": "38f1...",
  "attempt_id": 12,
  "artemis_session_id": "c8af...",
  "attempt_kind": "publish",
  "stage": "waiting_artemis",
  "device_serial_masked": "D123…00AC",
  "duration_ms": 215,
  "outcome": "running"
}
```

禁止记录：完整 caption、Prompt、预签名 URL、MinIO secret、Artemis token。异常堆栈只进服务端日志；API 返回清洗 message 与 request ID。

关键 event 名：

```text
publish_task_created
upload_confirmed
publish_task_claimed
publish_transition  # stage=刚完成阶段；只有阶段完成时带受控 duration_ms
object_download_started
object_download_succeeded
device_stage_started
device_stage_succeeded
artemis_attempt_created
artemis_submit_retried
artemis_status_changed
verification_started
verification_finished
publish_task_terminal
cleanup_resource_finished
worker_recovered_task
```

### 24.2 指标

若项目尚无 Prometheus，先通过结构化日志和只读状态 API提供；不要为本功能单独引入重型依赖。指标语义：

- `queueDepth` / `running` / `needsReview`：当前 owner 可见范围的 gauge；
- `tasksByStatus{status}`：当前任务状态快照 gauge，不是单调 counter；
- `attemptsByKindStatus{kind,status}`：当前 attempt 快照 gauge，不是单调 counter；
- `currentStageAgeSeconds{stage}`：当前仍处于各 stage 的数量/平均/最大年龄 gauge；终态 `done` 年龄会增长，不得称为已完成阶段 histogram；
- `cleanup{resource}`：当前 pending/failed cleanup gauge；
- `workerHeartbeatAgeSeconds`：Worker 心跳年龄 gauge。

已完成阶段耗时不从当前任务年龄倒推。每次真实 stage 变化后的 `publish_transition` 事件以旧 stage 作为 `stage` 并携带受控、非负 `duration_ms`；外部采集器据此聚合 `video_publish_stage_duration_seconds{stage}` histogram。无 stage 变化的 transition 其 `duration_ms=null`，不得进入 histogram。

### 24.3 告警建议

| 条件 | 等级 | 动作 |
| --- | --- | --- |
| Worker heartbeat > 30s | high | systemd/日志检查。 |
| running lease 过期 > 2 分钟 | high | recovery 是否工作。 |
| needs_review > 0 且持续 30 分钟 | medium | 页面提醒运营。 |
| cleanup failed > 10 或持续 1 小时 | medium | 检查 ADB/MinIO。 |
| waiting_device 持续 10 分钟 | medium | 解锁/连接手机。 |
| queue depth > 20 | low/容量 | 检查单任务耗时。 |

### 24.4 运维只读状态

建议 config 或 admin status 返回：

```json
{
  "worker": {"status": "ready", "heartbeatAgeSeconds": 2},
  "queueDepth": 3,
  "runningTaskId": "38f1...",
  "runningArtemisSessionId": "c8af...",
  "needsReviewCount": 0,
  "cleanupFailedCount": 0
}
```

普通用户看到业务友好状态；admin 可看实例/hostname/PID，但仍不暴露 token。
## 25. 安全与数据保留

- 上传接口必须校验登录、page permission、readwrite role 和 CSRF-style `X-Requested-With`；
- object key 由服务端生成并限制 prefix，防止路径穿越/覆盖他人对象；
- 文件名只作为展示数据，HTML escape；
- MinIO bucket 私有，GET 预览 URL仅在需要时短期生成；
- Artemis 只绑定固定 base URL，不能从请求 body 接收任意 URL；
- ADB adapter 不接受任意 shell；
- 设备 serial/package/profile 由服务端配置，用户不能篡改；
- Prompt 里的 caption 用 JSON 编码并标记为数据，locked package 形成第二道限制；
- 数据库任务/attempt 审计默认保留 180 天；真实保留期需与合规策略确认；
- succeeded/cancelled 对象立即删除；failed/needs_review 对象按保留策略；
- 删除 API v1 不提供，避免误删审计历史。
## 28. 已实现的最终安全边界

当前 migration head 为 `0066_publish_execution_fences`（parent `0065_publish_generation_identity`）。实现还明确保证：

- verify 只有在 Artemis execution `success` 且 `verdict` 严格等于 `published`、`not_published` 或 `inconclusive` 时才采信；`published` 才确认成功，单次 `not_published` 与 `inconclusive` 均保守进入 `needs_review/done`，不得自动重新发布；其他终态同样进入 `needs_review`；
- `cancelled`/`canceled` publish 与未知/格式错误的 Artemis 409 都是结果不确定，必须沿同 session 查询/核验，不能进入安全重发；
- `attempt_count` 是追加式审计数，`publish_budget_used` 独立表达预算；
- retention object cleanup pending/failed/leased 时，服务端不提供且拒绝 retry/replace；
- cleanup 删除期间持续续租，失去 owner 后旧 Worker 不写完成结果；每轮执行先持久化唯一 `execution_generation`，spool final/`.part` 与 device filename 都含该值，因此已开始的旧 cleaner 即使稍后恢复也只能命中退休路径；
- spool/object 由独立有界后台 cleanup coroutine 使用短 DB session 与 cleanup lease 处理，持续发布队列不会饿死清理，慢 MinIO 删除也不进入 publish-critical loop；
- Worker 心跳持久化 ADB 实际探测的 `ready|busy|offline|locked|unknown`；真正新建任务要求近期 ready 心跳并在缺失时返回结构化 503，已有幂等任务仍可读取/重放；
- 列表使用 `(created_at,id)` opaque keyset cursor，并只批量读取最新 attempt 与 publish/verify 计数；详情接口才加载完整 attempt 审计；
- 409 错误返回 `code/message/retryable/requestId/rowVersion/allowedActions`，浏览器始终按服务端 allowedActions 重绘；
- 0058 仅把成功 publish 或成功且 verdict=`published` 的 verify 视为发布确认；其他 verify 结果保留对象并进入 `needs_review`，且所有终态旧行都会清除 publish lease；
- attempt 的 `task_id/sequence_no/kind/related_attempt_id/artemis_session_id/prompt_version/prompt_snapshot/device_serial/device_path/target_app_package/artemis_profile/artemis_verification_level` 插入后不可变；active/new attempt 必须具有非空 profile/verification 快照，0065 predecessor 的 terminal history 可诚实保留 null 且不得恢复 active/resubmit；dispatch/resubmit 只读 attempt 快照，普通状态、结果、重试生命周期更新仍允许；
- confirm 后的下载以保存的 ETag 执行条件 GET；缺失对象和 ETag 不匹配都先由 cleanup selector 跟踪退休 key，且 missing 也必须等 PUT expiry + completion grace 后幂等确认 absence，完成后才允许 replacement；两者都在 staging/attempt 前失败；
- 原始浏览器 basename 与 object-key-safe 文件名分开持久化，续传按原始文件名和大小核对；
- verify prompt 同时包含 caption、源文件/对象身份、预期最新发布时间窗及时间/缩略图比对要求；
- ADB push 前必须确认受管相册无任意 `tts_erp_*.mp4` 残留；存在残留时不创建 Artemis attempt；
- confirm-upload 在入队前要求非空字符串 ETag；缺失时返回可重试 `UPLOAD_ETAG_MISSING` 并保持 awaiting_upload；
- 排队 snapshot 返回 `queuedAt/queuePosition/statusLabel/stageLabel`，列表批量计算队列位置；
- needs_review 摘要同时返回 `relatedPublishAttempt`，核验 dialog 不能把最新 verify session 冒充待核验 publish session；
- 结构化日志覆盖 claim、download/stage、attempt、verification、terminal、cleanup/recovery commit boundary，设备身份仅掩码；`/v2/video-publish/metrics` 返回 owner-scoped status/attempt/stage/cleanup 聚合与 heartbeat age；两者都不含 caption/Prompt/output/secret；
- migration/seed 仅自动授权 admin；operator session 权限必须在最终人工验证后显式授予；API key 默认也只有 admin tier 可访问 `/v2/video-publish/*`，未来 readwrite key 放行只能通过文档化的 `TTS_ERP_VIDEO_PUBLISH_ALLOW_READWRITE_API_KEYS=1` feature gate。

390/768/1440 像素视觉检查、模拟器全流程、SIGTERM 恢复以及 staging-only 真机检查仍按 §26 保留为人工发布门禁；本文不声称已执行这些检查。
