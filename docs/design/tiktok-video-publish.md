# TikTok 视频发布工作流技术方案

> 状态：Proposed
>
> 日期：2026-10-04
>
> 目标系统：ttsERP → MinIO → Android/ADB → Artemis → TikTok
>
> UI 规范：[`ui-style-system.md`](./ui-style-system.md)

## 1. 背景与目标

运营人员需要在 ttsERP 中创建一个发布任务，上传一个视频并填写文案。ttsERP 负责保存业务任务、把视频托管到 MinIO、全局串行调度 Android 设备、调用 Artemis 完成 TikTok UI 操作、持续查询执行状态，并在确认发布成功后清理视频。

本方案的核心边界是：

- **ttsERP 是业务任务与状态真相源**：管理上传、队列、重试、核验、审计和清理；
- **Artemis 是手机操作执行器**：接收固定 Prompt，在明确指定的 Android 设备和 App 内执行一次自动化；
- **MinIO 是临时媒资存储**：数据库保存 bucket/object key/ETag，不保存会过期的预签名 URL；
- **浏览器只调用 ttsERP**：不得直接调用 Artemis 或 ADB，也不自行推断发布结果。

### 1.1 已确认需求

1. 用户在 ttsERP 页面创建任务，选择一个视频并填写文案。
2. 视频上传到 MinIO；数据库保存文案与 MinIO 对象引用。
3. 后台通过 ADB 把视频放入手机专用相册，再调用 Artemis。
4. 全系统同一时间只执行一个视频发布任务。
5. Artemis 使用固定、版本化 Prompt，明确 App、视频来源、文案和成功/失败条件。
6. Artemis 支持按 `session_id` 查询状态，并支持通过 `device_serial` 指定设备。
7. Artemis 明确失败后可在同一个原任务下重试，所有执行历史必须保留。
8. 尽量自动完成状态确认；只有自动核验仍无法判定时才进入人工处理。
9. 前端任务状态必须定时刷新，并允许用户选择智能、固定间隔或关闭自动刷新。
10. 前端必须展示每次执行对应的完整 Artemis session ID，并提供一键复制，方便进入 Artemis 排障。
11. 发布成功后由后台清理手机、本地临时文件和 MinIO 视频。

### 1.2 非目标

- v1 不支持一个任务发布多个视频；
- v1 不支持同时向多台手机并行发布；
- v1 不让普通运营人员编辑 Artemis Prompt、App package 或设备序列号；
- v1 不实现 TikTok Shop Open API 发帖；发布仍通过手机 UI；
- v1 不把任意 ADB shell 命令暴露给 API 或前端；
- v1 不把 MinIO 预签名 URL 当作永久业务字段；
- v1 不保证在 TikTok 或设备完全不可用时零人工介入，但应把人工介入降到自动核验也无法收敛的最后一步。

## 2. 领域术语与不变量

| 术语 | 含义 |
| --- | --- |
| 发布任务（publish task） | 用户的一次业务意图：把一个视频和一份文案发布到 TikTok。重试不创建新的发布任务。 |
| 执行记录（attempt） | 一次 Artemis session。可用于正式发布或只读核验。一个发布任务可拥有多条执行记录。 |
| 正式发布执行（publish attempt） | 允许进入上传页面并最多点击一次最终发布按钮的 Artemis session。 |
| 核验执行（verify attempt） | 只检查作品页/草稿箱，不允许进入上传页面或点击发布的 Artemis session。 |
| 设备暂存文件 | ttsERP 通过 ADB 放入 `/sdcard/Movies/TTSERP/` 的任务专属文件。 |
| 业务成功 | Artemis 返回成功，或后续核验明确确认目标视频已经发布。 |
| 结果不确定 | Artemis 中断或失败时，无法证明最终发布动作未发生。 |

必须始终成立：

1. 一个发布任务恰好引用一个 MinIO 视频对象和一份文案。
2. 一个 Artemis `session_id` 只属于一条执行记录，并在网络重试时复用。
3. 同一个发布任务不能同时存在两条运行中的执行记录。
4. 全系统不能同时存在两个占用发布设备的任务。
5. 正式发布执行最多点击一次最终发布按钮。
6. `failed` 不等于“可以无条件重试”；结果不确定时必须先自动核验。
7. 发布成功与资源清理是两个维度；清理失败不能把已发布成功改成业务失败。

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
  ├─ ADB 推送到 /sdcard/Movies/TTSERP/<task_uuid>.mp4
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

    caption               text NOT NULL,
    original_filename     text NOT NULL,
    content_type          text NOT NULL,
    size_bytes            bigint NOT NULL,

    object_bucket         text NOT NULL,
    object_key            text NOT NULL UNIQUE,
    object_etag           text,
    object_uploaded_at    timestamptz,
    object_deleted_at     timestamptz,

    status                text NOT NULL,
    stage                 text NOT NULL,
    attempt_count         integer NOT NULL DEFAULT 0,
    next_attempt_at       timestamptz,

    target_device_serial  text NOT NULL,
    target_app_package    text NOT NULL,
    device_path           text,

    last_error_code       text,
    last_error_message    text,

    device_cleanup_status text NOT NULL DEFAULT 'not_started',
    device_cleanup_error  text,
    spool_cleanup_status  text NOT NULL DEFAULT 'not_started',
    spool_cleanup_error   text,
    object_cleanup_status text NOT NULL DEFAULT 'not_started',
    object_cleanup_error  text,

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
cleaning
done
```

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

    artemis_output        jsonb,
    artemis_error         text,
    steps_count           integer,
    retry_classification  text,

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
WHERE status IN ('submitting', 'queued', 'running');
```

`related_attempt_id` 用于表达“这次 verify 在核验哪次 publish”。

示例：

```text
任务 #1004
├── sequence 1 / publish / session A → failed（结果不确定）
├── sequence 2 / verify  / session B → 确认未发布
└── sequence 3 / publish / session C → success
```

主任务最终为 `succeeded`，三条 Artemis 历史全部保留。

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

数据库保存 `object_bucket + object_key + object_etag`。预签名 URL 只在响应中短暂存在，不落库。MinIO 必须为 ttsERP 页面来源配置仅允许 PUT/HEAD 所需 header 的 CORS；浏览器 URL 通过既有 `MINIO_PUBLIC_HOST` 重写，不能把服务器内部 `127.0.0.1` 地址返回给远端浏览器。

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

确认发布后先尝试清理设备文件，再释放设备执行槽；设备残留未清除时，下一任务停在 `waiting_device`，不能在同一相册继续 staging。spool 与 MinIO 清理可在业务成功后后台重试。任一清理失败只更新对应的 `*_cleanup_status=failed`，不能覆盖 `status=succeeded`。

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
8. 确认同一目录没有会干扰选择的其他活跃任务文件。

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

优先通过 `artemis-client`：

```python
handle = await client.submit(..., task_id=str(attempt.artemis_session_id))
result = await client.get_task(handle.task_id)
result = await client.wait_for_task(handle.task_id, timeout=1800)
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
- Planner 失败且 `/steps` 为 0；
- verify attempt 明确确认作品页和草稿箱都不存在目标内容。

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
明确未发布 → 新建 publish attempt（未超过上限时）
无法判断   → task needs_review
```

人工只处理 `needs_review`，不参与普通失败与超时。

## 8. 固定 Prompt 契约

Prompt 在 `tts_erp_v2/publishing/prompt.py` 中版本化；用户不能编辑原始 Prompt。

```python
PUBLISH_PROMPT_VERSION = "tiktok-video-publish-v1"
VERIFY_PROMPT_VERSION = "tiktok-video-verify-v1"
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

核验 Prompt 必须明确禁止任何发布动作。

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

## 10. 前端 UI 与交互设计

### 10.1 页面定位

- 页面：`GET /v2/pages/video-publish`
- 侧边栏：数据工具 → `视频发布`
- 页面权限：`page:video-publish`
- 主要用户：负责 TikTok 内容投放的运营人员
- 页面唯一工作：**安全地投递一个视频，并看清它是否真正完成发布。**

视觉继续使用全站“暖纸编辑体”，不新建颜色或字体体系：

```text
tokens.css → common.css → video-publish.css
```

页面的标志性元素是 **“单线发布轨道”**：它不是装饰，而是把“全局一次只执行一个任务”变成持续可见的信息结构。

```text
MINIO ●━━━━ 手机相册 ●━━━━ Artemis ◉━━━━ TikTok ○━━━━ 清理 ○
                              当前：执行中
```

周围界面保持克制：直角、纸面底色、墨色正文、陶土橙仅用于主操作和当前轨道节点；成功、警告、失败使用既有 `--ok/--warn/--danger`。

### 10.2 桌面信息架构

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ VIDEO DISPATCH · 视频发布                 设备 D123…00AC · 在线 · 空闲   │
│ 上传一个视频，系统将按队列串行发布到 TikTok                              │
├──────────────────────────────────────────────────────────────────────────┤
│ 当前发布轨道                                                             │
│ [MINIO]━━[手机相册]━━[Artemis]━━[TikTok]━━[清理]                         │
│ 当前无运行任务 / #1004 正在等待 Artemis                                  │
├──────────────────────────────────┬───────────────────────────────────────┤
│ 新建发布任务                     │ 本次投递                              │
│                                  │ App       TikTok                      │
│ [拖入 MP4 或选择文件]            │ 设备      D123…00AC                   │
│ video.mp4 · 14.7 MB              │ 相册      TTSERP                      │
│                                  │ 队列      前面 2 个任务               │
│ 文案                             │ 视频      video.mp4                   │
│ ┌──────────────────────────────┐ │ 文案      227 字                      │
│ │ ...                          │ │                                       │
│ └──────────────────────────────┘ │ [上传并加入发布队列]                  │
│ 227 / 4000                       │                                       │
├──────────────────────────────────┴───────────────────────────────────────┤
│ 任务记录 [全部][排队][执行中][失败][需核验][成功]                       │
│ 自动刷新 [智能 ▼] · 上次 10:42:15                         [立即刷新]    │
│ #1006 queued  video-a.mp4  Artemis 尚未创建             10:42 [查看]    │
│ #1005 failed  video-b.mp4  c8af5a6c…391d2 [复制]        10:31 [重试]    │
│ #1004 running video-c.mp4  41d9d326…8a104 [复制]        10:20 [查看]    │
└──────────────────────────────────────────────────────────────────────────┘
```

任务详情使用右侧 drawer；移动端改为全屏 dialog。主表不直接展开完整 Prompt、错误 trace 或执行步骤，避免队列扫描被长文本打断。

### 10.3 移动端布局

小于 720px 时：

1. 设备状态和轨道置顶；轨道允许横向滚动但节点文字不可缩成图标；
2. 新建任务改为单列：文件 → 文案 → 投递摘要 → 主按钮；
3. 任务表改为纵向任务条，每条显示状态、文件、创建时间和一个主操作；
4. 详情 drawer 改为全屏 dialog，返回后保留筛选与滚动位置；
5. 不使用 hover 才能发现的信息。

### 10.4 创建任务交互

初始状态：

- 文件选择区显示允许格式与服务端下发的大小上限；
- 文案为空，显示字符计数；
- “上传并加入发布队列”禁用；
- 右侧投递摘要始终展示固定 App、设备和相册，避免用户误以为可自由选择目标。

选择文件后：

1. 立即在浏览器校验扩展名、MIME 和大小；
2. 展示文件名、格式化大小以及“重新选择”；
3. 使用本地 object URL 显示静音视频预览、时长和分辨率，让运营在入队前确认没有选错视频；重新选择或离开页面时必须 `URL.revokeObjectURL()`；
4. 浏览器校验只用于快速反馈，不能代替服务端校验；
5. 不在选择文件后自动上传，避免用户尚未确认文案时产生废弃对象。

输入文案时：

- 保留换行、emoji 和 `#` 标签；
- 显示 `当前字符数 / 服务端上限`；
- 超限时计数变为危险色，主按钮禁用；
- 不自动清洗、翻译或重写；如需要运营备注，必须使用独立字段，不能混入文案后再猜测删除。

点击“上传并加入发布队列”：

1. 前端执行最终本地校验；
2. 打开确认 dialog，原样展示文件、完整文案、TikTok、目标设备和提示“设备空闲时可能立即开始”；
3. 用户点击“确认加入队列”后生成一次 `clientRequestId`；
4. POST 创建任务；
5. 使用 `XMLHttpRequest` PUT 到 MinIO，以获得真实上传进度；
6. PUT 完成后调用 `confirm-upload`；
7. confirm 成功后清空表单，把新任务插入队列顶部，并聚焦成功提示。

上传过程中：

- 文件与文案锁定，防止上传对象与数据库元数据不一致；
- 主按钮替换为进度，例如 `上传中 42%`；
- 提供“取消上传”，终止 XHR 后调用任务 cancel；
- 页面离开时，如果存在上传中的 XHR，使用原生 beforeunload 提示；普通排队/运行任务不阻止离开页面。

上传失败：

- 保留本地文件引用和文案；
- 显示具体错误：网络中断、URL 过期、对象校验失败或服务端拒绝；
- 提供“重试上传”，对同一 task 请求新 upload URL；
- 重试上传不创建新的业务任务。

浏览器刷新后无法恢复 `File` 对象。未完成任务显示在列表中，操作为“重新选择文件继续”或“取消任务”；重新选择时前端检查文件名和大小，后端仍以对象校验为准。

### 10.5 当前发布轨道

轨道只显示全局唯一的当前设备任务：

| stage | 当前节点 | 页面文案 |
| --- | --- | --- |
| downloading | MinIO | 正在下载并校验视频 |
| staging_device | 手机相册 | 正在写入 TTSERP 相册 |
| dispatching_artemis | Artemis | 正在创建手机自动化任务 |
| waiting_artemis | Artemis/TikTok | Artemis 正在操作 TikTok |
| verifying | TikTok | 正在核验是否已经发布 |
| cleaning | 清理 | 已确认发布，正在删除临时视频 |
| done | 全部完成 | 轨道收起，任务进入历史记录 |

节点状态必须同时使用图形、文字和 `aria-current="step"`，不能只靠颜色。阶段切换采用一次 150–200ms 的轨道填充过渡；`prefers-reduced-motion` 下关闭动画。

轨道旁显示：

- 任务编号与视频文件名；
- 当前 attempt 类型及次数；
- 当前 Artemis session ID；桌面端显示完整 UUID，窄屏可视觉省略中段，但 `title`、无障碍名称和“复制”操作必须保留完整值；
- 当前阶段开始时间与已耗时；
- “查看详情”，不提供运行中普通重试。

### 10.6 队列与任务列表

筛选项：

```text
全部 | 排队 | 执行中 | 失败 | 需核验 | 成功 | 已取消
```

列表字段：

- 任务编号；
- 视频文件名和大小；
- 文案首行摘要；
- 业务状态与当前 stage；
- 发布/核验执行次数；
- 当前或最近一次 Artemis session ID，使用等宽字体并提供一键复制；尚未创建 attempt 时明确显示“尚未创建”；
- 创建人和创建时间；
- 服务端返回的主要动作。

前端不得根据状态自行拼出允许动作。列表/详情响应提供：

```json
{
  "allowedActions": ["view", "retry", "verify", "cancel", "retry_cleanup"]
}
```

页面只渲染服务端允许的动作，服务端仍须在写端点再次校验状态。

### 10.7 状态与操作矩阵

| task 状态/阶段 | 主操作 | 次操作 | 禁止 |
| --- | --- | --- | --- |
| awaiting_upload | 重新选择文件继续 | 取消任务 | 发布重试 |
| queued | 查看队列位置 | 取消任务 | 修改视频/文案 |
| waiting_device | 查看设备原因 | 取消任务 | 绕过指定设备 |
| running/* | 查看详情 | 无 | 重试、再次提交、普通取消 |
| failed（可重试） | 重试原任务 | 查看执行历史 | 新建重复任务 |
| needs_review | 自动核验 | 查看失败步骤 | 直接重试发布 |
| succeeded | 查看详情 | 清理失败时重试清理 | 再次发布 |
| cancelled | 查看详情 | 无 | 恢复原 attempt |

`needs_review` 不显示普通“重试”按钮。若以后提供管理员强制重试，必须是单独的 admin 权限和二次确认，并明确提示可能重复发布；不纳入 v1。

### 10.8 任务详情 drawer

详情分四个固定区域：

1. **发布内容**：视频元数据、完整文案、MinIO 对象状态；
2. **目标与进度**：App、设备、相册、当前 stage、队列/耗时；
3. **执行历史**：按 sequence 倒序展示 publish/verify attempt；
4. **资源清理**：手机、本地 spool、MinIO 的独立清理结果。

attempt 行展示：

```text
第 3 次执行 · 正式发布
success · Artemis ID c8af5a6c-4158-49c3-9141-13bc69b391d2 · D123…00AC
开始 10:42:12 · 完成 10:46:31 · 4m19s
[查看错误/输出] [复制 Artemis ID]
```

Artemis ID 不是敏感凭据，应在详情中完整展示，复制内容不得带前后缀或省略号，方便直接粘贴到 Artemis 查询。一个原任务存在多次 publish/verify attempt 时，每一条都展示自己的 ID，不能只保留最后一个。

Prompt 快照和 Artemis 原始 output 默认折叠，仅 admin 或诊断权限可查看；文案与错误文本按纯文本渲染，禁止 `innerHTML` 注入。

### 10.9 重试交互

`failed` 且服务端返回 `retry` 时：

1. 用户点击“重试原任务”；
2. dialog 显示上一条 attempt 的失败阶段、错误、已尝试次数；
3. 明确说明“将使用原视频和原文案创建新的 Artemis 执行记录”；
4. 用户确认后 POST `/retry`；
5. 服务端原子检查状态并排队；
6. UI 更新为 queued，不复制一条新的业务任务行。

若 MinIO 对象已经丢失或清理，服务端不返回 `retry`，而返回 `replace_upload`；UI 要求重新上传，不得生成必然失败的 attempt。

### 10.10 自动核验交互

通常 Worker 自动进入 verifying，用户只看到轨道节点变化。若自动策略停在 `needs_review`：

- 主按钮为“再次自动核验”，不是“人工检查”或“重新发布”；
- 点击后显示只读说明：“核验只查看作品页和草稿箱，不会发布内容”；
- verify attempt 运行时任务回到 `running/verifying`；
- 已发布 → 成功；明确未发布 → 服务端按重试预算自动排队；仍无法判断 → 回到 `needs_review` 并展示原因。

### 10.11 定时刷新、轮询与竞态

页面只轮询 ttsERP，不直接查询 Artemis。`video-publish.js` 与现有页面一致，从 `location.pathname` 推导 `/tts` 等部署前缀；API 和静态资产不得使用破坏子路径部署的绝对根路径。

任务工具栏提供可见的定时刷新控制：

```text
自动刷新 [智能 | 2 秒 | 5 秒 | 10 秒 | 30 秒 | 关闭]
上次刷新 10:42:15 · 下次约 2 秒后              [立即刷新]
```

默认选择“智能”，规则为：

- 存在运行中的 publish/verify attempt：`/current` 每 2 秒；当前列表每 5 秒；
- 无运行任务但存在排队任务：当前任务/列表每 8 秒；
- 只有终态历史任务：列表每 30 秒；
- 页面隐藏：暂停高频轮询，最多每 30 秒一次；恢复可见时立即刷新；
- 用户选择固定间隔后，当前任务、当前列表页和已打开详情都使用该间隔；
- 用户选择“关闭”后只保留“立即刷新”，运行中轨道显示“自动刷新已暂停”，避免用户误以为状态仍实时；
- 用户选择保存在 `localStorage`，只保存刷新偏好，不保存任务、文案或凭据。

轮询实现约束：

1. 定时器必须在上一次请求结束后再安排下一次，禁止 `setInterval` 造成慢请求重叠；
2. 使用 `AbortController` 与单调 generation，旧响应不得覆盖新筛选、新详情或更新后的 attempt；
3. 使用 `If-None-Match`，304 时只更新时间提示，不重建 DOM；
4. 打开任务详情时同步刷新该任务详情，保证新增 attempt 和 Artemis ID 在页面出现；
5. 每次任务状态、stage、current attempt 或 Artemis ID 变化时，更新轨道、列表行和 drawer，但不得抢走当前键盘焦点；
6. 连续刷新失败时采用 5/10/30/60 秒退避，顶部显示“状态刷新失败，正在重试”，手动刷新成功后恢复用户选择的频率；
7. 页面销毁时取消 timer 和所有未完成请求；
8. 后续可升级 SSE，但 v1 不为单页状态建立新的实时基础设施。

列表顶部始终显示上次成功刷新时间。前端可以对“已提交/已取消”做临时 busy 状态，但任务业务状态和 Artemis ID 始终以服务端响应为准。

### 10.12 空状态、错误和文案

空状态应给下一步动作：

- 无任务：`还没有发布任务。选择一个 MP4 并填写文案开始。`
- 队列为空：`当前没有等待中的视频。`
- 设备不可用：`发布设备离线。任务会保留在队列中，设备恢复后继续。`
- needs_review：`系统无法确认这次操作是否已经发布。先运行自动核验，避免重复发布。`

错误必须说明发生位置和可执行动作：

- `视频上传中断。文件和文案已保留，可以重试上传。`
- `MinIO 对象校验失败：实际大小与所选文件不一致，请重新上传。`
- `目标设备已锁屏。任务仍在队列中，解锁后会自动继续。`
- `Artemis 在点击发布后失去连接，系统正在检查作品页。`

禁止只显示 `操作失败`、`未知错误` 或原始 Python/HTTP 堆栈。

### 10.13 可访问性与安全

- 文件区同时保留原生 `<input type="file">`，拖放不是唯一入口；
- dialog 使用原生 `<dialog>` 或完整 focus trap，并在关闭后恢复触发按钮焦点；
- 状态提示使用有界 `aria-live="polite"`，上传失败使用 `role="alert"`；
- 进度条使用 `role="progressbar"` 和数值属性；
- 所有状态文字都能在不识别颜色的情况下理解；
- 视频文件名、文案、错误和 Artemis output 一律 textContent/HTML escape；
- 浏览器不获取 MinIO access key、Artemis token 或完整设备控制能力；
- 页面不暴露任意 Prompt 编辑器或任意 ADB 命令输入框。

### 10.14 前端状态机

```text
FORM_EMPTY
  └─ file/caption valid → FORM_READY
       └─ click submit → CONFIRMING
            └─ confirm → CREATING_TASK
                 └─ ticket → UPLOADING
                      ├─ progress → UPLOADING
                      ├─ cancel → CANCELLING → FORM_READY
                      ├─ upload error → UPLOAD_FAILED → retry → UPLOADING
                      └─ PUT complete → CONFIRMING_OBJECT
                           ├─ confirm error → UPLOAD_FAILED
                           └─ success → QUEUED → FORM_EMPTY
```

任务列表与创建表单是两个独立状态域。列表刷新失败不能清空正在编辑的文件和文案；上传失败也不能停止当前运行轨道的轮询。

## 11. 权限

| 操作 | 最低角色 |
| --- | --- |
| 打开页面、查看任务/attempt | readonly + `page:video-publish` |
| 创建、上传确认、取消排队任务 | readwrite + `page:video-publish` |
| 重试、自动核验、重试清理 | readwrite + `page:video-publish` |
| 查看完整 Prompt/原始 Artemis output | admin |
| 强制跳过核验直接重试 | v1 不提供 |

`accounts/pages.py` 新增 `video-publish`，默认授权 `operator` 与 `admin`；viewer 默认不授权。访问策略对 GET 与写方法分别分类，不能把整个 prefix 粗暴设为 readwrite。

## 12. 模块与文件布局

```text
tts_erp_v2/publishing/
├── __init__.py              # 窄 public interface
├── domain.py                # 状态、命令、outcome、转换规则
├── submission.py            # 创建票据、confirm、cancel
├── dispatcher.py            # dispatch_one / recover_active
├── repository.py            # claim 与持久化转换
├── prompt.py                # publish/verify 固定 Prompt
├── artemis_client.py        # Artemis SDK adapter
├── adb_device.py            # 受控 ADB adapter
├── object_store.py          # video bucket adapter
└── worker.py                # 独立进程入口

tts_erp_v2/db/models/publishing.py
tts_erp_v2/api/v2/video_publish.py
tts_erp_v2/templates/pages/video-publish.html
tts_erp_v2/static/js/video-publish.js
tts_erp_v2/static/css/video-publish.css
alembic/versions/0053_video_publish.py
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

## 15. 测试策略

测试只能通过：

```bash
bash scripts/test_isolated.sh ...
```

### 15.1 Domain 与数据库

- 合法/非法状态转换；
- 原任务多 attempt；
- 相同 session ID 幂等；
- 同任务活跃 attempt 唯一；
- 全局 running task 唯一；
- `FOR UPDATE SKIP LOCKED` 并发领取；
- publish/cleanup 状态正交；
- retry/verify 的服务端 allowedActions。

### 15.2 Adapter

- MinIO ticket、HEAD、下载校验和删除失败；
- ADB 设备离线、锁屏、push、size 和 MediaStore；
- Artemis 指定 `device_serial`、相同 session 重提、状态轮询、404→全局状态 fallback；
- completed/success/failed/rejected/timeout 映射；
- steps=0 安全失败与最终点击后的不确定失败分类。

### 15.3 API

- create 的 `clientRequestId` 幂等；
- confirm 前后状态；
- readonly/readwrite/admin 权限；
- 非法状态返回 409 + allowedActions；
- cancel/retry/verify/cleanup retry；
- 列表不泄露 Prompt、token、完整设备 serial 或大段 output。

### 15.4 前端

- 文件格式/大小、文案计数和按钮可用性；
- XHR 进度、取消、URL 过期后重试；
- 创建双击只生成一个任务；
- 状态轨道节点与服务端 stage 映射；
- needs_review 不显示直接重试；
- drawer 执行历史、每条 attempt 的完整 Artemis ID 和清理状态；
- Artemis ID 一键复制，复制值为完整 UUID，排队未建 attempt 时显示“尚未创建”；
- 智能/固定/关闭三类定时刷新、手动刷新、页面可见恢复、失败退避和 stale response 防护；
- 401 登录跳转、403 只读模式；
- 键盘、dialog 焦点、aria-live、reduced motion；
- 390px、768px、1440px 三档视觉冒烟。

禁止在测试中向真实 TikTok 发布。端到端测试默认使用假 Artemis/ADB adapter；真实设备验证必须先 dry-run，并由用户显式确认。

## 16. 实施阶段

### Phase 1：持久化与上传

- migration、models、MinIO video bucket；
- create/upload-url/confirm/list/detail/cancel；
- 页面创建表单、上传进度和队列列表。

### Phase 2：单任务执行

- 独立 publish worker；
- ADB staging 与 MediaStore 校验；
- Artemis SDK adapter、指定设备、状态轮询；
- 当前发布轨道和 attempt 详情。

### Phase 3：自动恢复与核验

- restart recovery；
- safe retry classifier；
- verify attempt 与固定核验 Prompt；
- needs_review 最后兜底；
- 清理重试。

### Phase 4：真机受控验证

1. 使用模拟器验证上传、队列、ADB staging 和失败恢复；
2. 真机只执行 staging + MediaStore dry-run；
3. 用户确认后创建一条真实发布任务；
4. 验证发布、状态闭环和三处清理；
5. 再开放普通运营角色。

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
