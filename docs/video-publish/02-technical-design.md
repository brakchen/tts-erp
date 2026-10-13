# TikTok 视频发布：技术设计

> **文档导航**：[产品方案](01-product-proposal.md) · [技术设计](02-technical-design.md) · [实施计划](03-implementation-plan.md) · [测试计划和结果](04-test-plan-and-results.md)

> 版本：2026-10-12 与产品方案对齐修订稿。业务规则以[产品方案](01-product-proposal.md)为唯一来源；本文描述实现这些规则的目标架构与契约。
> 本文是目标设计；§21 如实列出当前实现与目标的差距，差距不代表规则未确定。产品目标冲突时先修订《产品方案》；实现偏离本文时必须在《实施计划》中记录迁移，而不是在调用方增加兼容分支。

## 1. 文档职责

覆盖领域模型、状态机、对象生命周期、Worker、API、前端状态、可观测性与安全边界。所有业务规则（确认按钮、取消边界、重试、权限、保存）来自《产品方案》，本文只决定技术做法，不重新提出业务问题。

## 2. 已确认规则与技术决策映射

| 产品方案规则 | 本文技术决策 |
| --- | --- |
| 人员点击“确认已发布”才转成功，记录点击时间与实际操作人 | 新增 `confirm-published` 写接口与 `video_publish_confirmations` 审计表；数据库唯一约束保证第一笔有效（§7.4、§14.3、§16.7） |
| AI/Artemis 报告成功不直接置成功 | `ARTEMIS_SUCCESS` 与 verify `published` 都只进入 `needs_review`（§15） |
| 成功提交 Artemis 后禁止取消/停止，参数永久只读 | 任务记录 `params_locked_at`（首次 admission 时间）；取消/编辑接口统一校验该字段；不提供任何 stop 接口（§6.5、§9.7、§16.4） |
| 失败无限次手动重试，不自动重试 | 删除发布预算与自动重排；失败一律落 `failed/done`，仅人工 `retry` 重新排队（§7.1、§7.2） |
| 未登录 TikTok 直接失败并写进 Prompt | Prompt 由“用户可编辑正文 + 服务端追加的强制安全段”组成，未登录失败条款不可被编辑移除（§8） |
| 所有 MinIO 原视频不删除 | 对象生命周期只有“已保留”，删除路径整体移除；清理只剩设备/服务器临时副本（§5.3） |
| 任务与日志暂不设置自动删除 | 不配置任何 retention/到期回收任务（§20） |
| 页面授权是唯一人员门槛 | 所有读/写接口只检查登录 + `page:video-publish`；不按创建人过滤；指标为页面级全局口径（§9.1、§19.2） |
| 同设备串行、不同设备并行 | 唯一约束改为“每台设备一个 running”；Worker 按设备并行领取（§4.1、§6.1） |
| 默认请求结束后等 5 秒刷新，可关闭或手动刷新 | 固定 5 秒间隔（响应完成后计时），无智能模式（§18.5） |

## 3. 总体架构

```text
浏览器
  │
  ├─ POST 创建上传票据（视频元数据 + 文案 + client_request_id）
  │        ├─ INSERT publishing.video_publish_tasks
  │        └─ 返回 MinIO 预签名 PUT URL
  ├─ PUT 视频 ───────────────────────────────────────────→ MinIO（对象永久保留）
  ├─ POST confirm-upload
  │        ├─ HEAD 校验 size/content-type/ETag
  │        └─ task → pending/queued（进入目标设备队列）
  └─ POST confirm-published（人员核验后）
           └─ 同一事务：task → succeeded + 写确认审计（操作人/点击时间）

独立 tts-erp-publish worker（按设备并行）
  │
  ├─ PostgreSQL 原子领取：每台设备至多一个 running
  ├─ MinIO 下载到本地 spool，校验对象
  ├─ ADB 推送到 /sdcard/Movies/TTSERP/tts_erp_<execution_generation>.mp4
  ├─ MediaStore 扫描并确认相册可见
  ├─ POST Artemis /api/run（固定 session_id + device_serial）
  ├─ GET /api/sessions/{session_id} 持续查询
  ├─ 报告成功或结果不确定 → needs_review/done（等待人工确认按钮）
  ├─ 明确失败（含 TikTok 未登录）→ failed/done（等待人工无限次重试）
  └─ 本轮结束后清理设备与本地 spool 临时副本（MinIO 不动）
```

### 3.1 为什么使用独立发布 Worker

视频发布可能持续数分钟，且涉及 ADB、Artemis 轮询和手机状态。它不应运行在 FastAPI 请求生命周期中，也不应占用现有 TikTok 数据同步作业。

```text
tts-erp.service          # HTTP API / 页面
tts-erp-sync.service     # 既有数据同步
tts-erp-publish.service  # 视频发布调度与恢复
```

入口：

```bash
python -m tts_erp_v2.publishing.worker
```

Worker 内每台目标设备一个执行槽，使用短事务更新状态；等待 Artemis 时不得持有数据库事务或行锁。

## 4. 数据模型

`publishing` schema。相对既有实现的变更：**新增确认审计表、任务确认/锁定/覆盖字段；运行互斥从全局改为按设备；发布预算字段退役；MinIO 对象清理字段退役**。

### 4.1 `publishing.video_publish_tasks`

一行代表一个用户原始发布任务：

```sql
CREATE TABLE publishing.video_publish_tasks (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id             uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    client_request_id     uuid NOT NULL UNIQUE,
    created_by_user_id    bigint,          -- 仅追溯，不作权限过滤
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
    -- MinIO 对象永不删除：不设置 object_deleted_at 语义，旧列保留仅作历史兼容。

    prompt_override       text,            -- 提交前可编辑的 Prompt 正文；为空用默认模板正文
    target_device_serial  text NOT NULL,   -- 提交前可编辑；锁定后只读
    target_app_package    text NOT NULL,   -- 提交前可编辑；锁定后只读
    params_locked_at      timestamptz,     -- 首次 Artemis admission 成功时间；非空后参数永久只读且禁止取消/停止

    confirmed_at          timestamptz,     -- 服务端接收确认点击的时间（= 点击时间）
    confirmed_by_user_id  bigint,          -- 实际点击的认证用户
    confirmed_by_key_hash text,            -- API-key 调用者的不可逆 hash（如有）

    cleanup_intent        text NOT NULL DEFAULT 'none',
    cleanup_lease_owner   text,
    cleanup_lease_expires_at timestamptz,
    cleanup_heartbeat_at  timestamptz,

    status                text NOT NULL,
    stage                 text NOT NULL,
    stage_started_at      timestamptz NOT NULL DEFAULT now(),
    attempt_count         integer NOT NULL DEFAULT 0,   -- 追加式审计计数，不作次数门槛
    next_attempt_at       timestamptz,
    cancel_requested      boolean NOT NULL DEFAULT false, -- 仅 admission 前可置位

    lease_owner           text,
    lease_expires_at      timestamptz,
    heartbeat_at          timestamptz,
    row_version           integer NOT NULL DEFAULT 1,

    execution_generation  uuid UNIQUE,
    device_path           text,
    spool_path            text,

    last_error_code       text,
    last_error_message    text,
    last_verify_verdict   text,            -- published | not_published | inconclusive；仅线索，不改变状态

    device_cleanup_status text NOT NULL DEFAULT 'not_started',
    device_cleanup_error  text,
    device_cleanup_attempts integer NOT NULL DEFAULT 0,
    device_cleanup_next_attempt_at timestamptz,
    spool_cleanup_status  text NOT NULL DEFAULT 'not_started',
    spool_cleanup_error   text,
    spool_cleanup_attempts integer NOT NULL DEFAULT 0,
    spool_cleanup_next_attempt_at timestamptz,
    -- object_cleanup_* 旧列退役：不再调度对象删除，新行固定 'not_started'，API 不暴露删除入口。

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

设备与 spool 的 `*_cleanup_status` 取值：

```text
not_started | pending | succeeded | failed
```

**每台设备至多一个运行任务**（替代旧的全局唯一索引）：

```sql
CREATE UNIQUE INDEX uq_video_publish_one_running_per_device
ON publishing.video_publish_tasks (target_device_serial)
WHERE status = 'running';
```

数据库部分唯一索引是跨进程最终护栏；进程内并发限制只是附加保护。

### 4.2 `publishing.video_publish_attempts`

一行对应一个 Artemis session。正式发布与只读核验都写入该表：

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
    prompt_snapshot       text NOT NULL,       -- 正文 + 服务端强制安全段的最终文本
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
    failure_classification text,               -- 仅诊断展示，不驱动自动动作

    submitted_at          timestamptz,
    started_at            timestamptz,
    finished_at           timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),

    UNIQUE (task_id, sequence_no)
);
```

同一任务只能有一条活跃执行：

```sql
CREATE UNIQUE INDEX uq_video_publish_task_active_attempt
ON publishing.video_publish_attempts (task_id)
WHERE status IN ('created', 'submitting', 'queued', 'running', 'unknown');
```

`related_attempt_id` 表达“这次 verify 在核验哪次 publish”。所有 attempt 只追加，不覆盖；verify 的任何结论都不会把任务推进为 `succeeded`。

### 4.3 `publishing.video_publish_confirmations`（新增）

人工“确认已发布”按钮的审计真相源：

```sql
CREATE TABLE publishing.video_publish_confirmations (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id        bigint NOT NULL
                   REFERENCES publishing.video_publish_tasks(id)
                   ON DELETE RESTRICT,
    action         text NOT NULL,            -- v1 固定 confirm_published
    from_status    text NOT NULL,            -- needs_review | failed
    to_status      text NOT NULL,            -- succeeded
    operator_user_id   bigint,               -- 本系统认证用户；不是任务创建人、不是手机 TikTok 账号
    operator_key_hash  text,
    request_id     uuid NOT NULL UNIQUE,     -- 浏览器生成的幂等键
    created_at     timestamptz NOT NULL DEFAULT now()  -- 服务端接收时间 = 点击时间
);

-- 每个任务只有第一笔有效确认；并发/重放由唯一约束兜底
CREATE UNIQUE INDEX uq_video_publish_confirm_once
ON publishing.video_publish_confirmations (task_id)
WHERE action = 'confirm_published' AND to_status = 'succeeded';
```

`operator_user_id` 来自当前请求的认证主体，客户端不能传操作人；`created_at` 由数据库时间生成，客户端不能传时间。历史自动成功任务没有该表记录，详情如实显示来源，不伪造人和时间。

### 4.4 `publishing.worker_heartbeats`

```sql
CREATE TABLE publishing.worker_heartbeats (
    instance_id   text PRIMARY KEY,
    hostname      text NOT NULL,
    pid           integer NOT NULL,
    status        text NOT NULL, -- starting | ready | stopping
    device_status jsonb NOT NULL DEFAULT '{}',  -- {serial: ready|busy|offline|locked|unknown}
    device_message text,
    version       text,
    started_at    timestamptz NOT NULL,
    heartbeat_at  timestamptz NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
```

Worker 每 5 秒 upsert；API 以 `heartbeat_at >= now() - interval '15 seconds'` 判定进程可用。`device_status` 按设备分别记录探测结果；某台设备 offline/locked/busy 不阻止创建排队任务，Worker 不可用或必需配置缺失才写阻断原因。

## 5. 上传与对象生命周期

### 5.1 上传模式

复用 `tts_erp_v2/storage/minio_client.py` 预签名上传：

1. ttsERP 创建 `awaiting_upload` 任务并生成 object key；
2. 浏览器直接 PUT 到 MinIO；
3. 浏览器调用 confirm；
4. ttsERP 通过 HEAD 校验对象并保存 ETag；
5. 校验通过后进入 `queued`。

object key：

```text
video-publish/YYYY/MM/<task_public_id>/<sanitized_filename>.mp4
```

数据库保存 `object_bucket + object_generation + object_key + object_etag`，并持久化该 generation 已签发 PUT ticket 的最晚 `object_upload_expires_at`。恢复上传（replace）生成从未复用的新 generation/key；新旧 generation 的对象都保留。MinIO 为页面来源配置仅允许 PUT/HEAD 所需 header 的 CORS；浏览器 URL 通过 `MINIO_PUBLIC_HOST` 重写。

### 5.2 校验

v1 仅接受 MP4。上限由服务端配置下发，服务端始终重新校验：

- 扩展名 `.mp4`；`Content-Type: video/mp4`；
- `0 < size_bytes <= configured_max_size`；
- confirm 时对象存在且实际 size 与创建时一致；
- 下载后计算 SHA-256；单段 ETag 可校验 MD5。

### 5.3 清理与保留规则

| 资源 | 规则 |
| --- | --- |
| MinIO 原视频 | **任何状态都保留**：成功、失败、待确认、取消、重试后均不删除；没有页面删除入口、后台删除任务或过期回收 |
| 手机临时副本 | 本轮结束且不再使用后安全删除或隔离；不删 TikTok 已发布作品、其他任务或用户视频 |
| 服务器 spool 副本 | 本轮结束后可后台删除；不影响 MinIO 原对象 |
| 任务/attempt/确认审计 | 暂不设置自动删除；不新增到期回收 |

设备清理只针对服务端生成的精确 `device_path`：删除文件、删除/重扫该 MediaStore entry，并轮询确认两者都不存在；失败保持 `device_cleanup_status=failed`。设备预检同时检查文件系统与 MediaStore 残留；未清除时该设备的下一任务停在 `waiting_device`（不影响其他设备）。spool 清理必须逐个删除精确的 `video.mp4` 与 `video.mp4.part`，不得用 wildcard。清理失败不改写业务状态。

设备清理失败时**允许任务排队、由领取门禁阻塞**（不再二选一待定）：重试/新建进入 `queued` 都允许，Worker 领取前检查该设备的 cleanup 状态，不安全则不领取。API 层不做 409 拒绝，页面如实显示“等待设备清理”。

## 6. 调度、设备与 Artemis 契约

### 6.1 按设备领取

Worker 对每台已知设备独立领取：

```sql
SELECT id
FROM publishing.video_publish_tasks
WHERE status = 'pending'
  AND stage IN ('queued', 'waiting_device')
  AND target_device_serial = :serial
  AND (next_attempt_at IS NULL OR next_attempt_at <= now())
ORDER BY queued_at, id
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

领取前检查该设备：无 running 任务（唯一索引兜底）、无 pending/failed/leased 的设备清理。同一事务改为 `running/downloading` 并写 lease。一台设备的离线、锁屏或清理等待不影响其他设备领取。

### 6.2 设备预检与暂存

正式调用 Artemis 前执行：

1. `adb devices` 确认目标 serial 在线；
2. 确认目标 package 已安装；
3. 下载视频到 spool，`.part` 后原子 rename；
4. 推送到 `/sdcard/Movies/TTSERP/tts_erp_<execution_generation>.mp4`；
5. 比较设备文件大小；
6. 触发 MediaStore 扫描并确认相册可见；
7. 确认受管相册无其他 `tts_erp_*.mp4` 残留。

ADB adapter 只暴露受控方法，不提供任意 `shell(command)`：

```python
check_device(serial)
check_package(serial, package)
stage_video(serial, local_path, device_path)
verify_media_visible(serial, device_path)
remove_staged_video(serial, device_path)
```

### 6.3 指定设备提交

```json
{
  "goal": "<正文 + 强制安全段>",
  "session_id": "<attempt artemis_session_id>",
  "profile": "pro",
  "device_serial": "D123084100AC",
  "locked_app_package": "com.zhiliaoapp.musically",
  "verification_level": "strict",
  "ingress": "tts-erp-video-publish"
}
```

同一执行记录的网络重试复用同一个 `session_id`；只有人工重试新一轮才生成新 session ID。admission 成功的同事务写入 `params_locked_at`——从此参数永久只读、禁止取消/停止，后续重试沿用锁定快照。

### 6.4 状态查询

```text
GET /api/sessions/{session_id}        # 单任务持久化状态
GET /api/status                       # 实时队列与 active task fallback
GET /api/sessions/{session_id}/steps  # 执行步骤与失败位置
GET /api/devices                      # 设备在线/忙碌状态
```

Artemis 终态：`completed | success | failed | cancelled | canceled | rejected`。等待超时不是业务失败；Worker 结束本轮等待、保留同一 attempt 为 `running`，下一轮继续查询同一 session。

### 6.5 取消边界

- `cancel` 只在 `params_locked_at IS NULL` 时可用：
  - pending（`awaiting_upload/queued/waiting_device`）：同事务直接 `cancelled/done`；
  - running 且尚未 admission（`downloading/staging_device/dispatching_artemis`）：置 `cancel_requested=true`，Worker 在下一个阶段边界停止、不得提交 Artemis，完成设备/spool 安全收尾后落 `cancelled/done`；
  - 提交中（`dispatching_artemis` 正在等待 admission 响应）：返回 409 `TASK_SUBMITTING`，要求调用方稍后按真实状态重试，不得假装已取消。
- `params_locked_at` 非空（曾成功提交）：页面和接口一律拒绝取消/停止，返回 409 `TASK_ALREADY_SUBMITTED`，不发送任何 Artemis stop。
- 取消成功不删除 MinIO 原视频。

## 7. 重试、核验与人工确认

### 7.1 失败即停，人工重试

本轮明确失败（含 TikTok 未登录、MinIO 下载失败、ADB/MediaStore 失败、Artemis 明确失败）一律落 `failed/done`，**不自动重排、无次数上限、无退避预算**。页面提供“重试原任务”：

- 仅 `failed`、无活跃 attempt、对象有效时可点；
- 同一事务：清主表错误与 `completed_at`、写新 `queued_at`、`status=pending/stage=queued`；
- 历史 attempt 与计数保留，仅作展示，不构成门槛；
- 每轮正式发布最多一次最终发布动作由 attempt 唯一活跃约束保证。

### 7.2 传输重试（不变）

提交请求超时或响应丢失：不创建新 attempt，复用原 `artemis_session_id`，先查询再以相同 ID 重提，依靠 Artemis 幂等 admission 防重复执行。绝对禁止 transport timeout 后生成新 session ID。

### 7.3 只读辅助核验

`needs_review` 任务可由人工触发 `verify`（创建 `kind=verify` attempt，占用该设备槽）。核验 Prompt 只允许打开作品页/草稿箱核对，禁止进入上传页、禁止发布。结果写入 `last_verify_verdict` 与 attempt 审计，**任务状态保持 `needs_review/done` 不变**——核验只是线索，不是成功判据。`publish_action_observed`、`session_missing` 等不确定结果直接落 `needs_review/done`，不再自动创建 verify。

### 7.4 人工确认成功（唯一成功路径）

```http
POST /v2/video-publish/tasks/{task_id}/confirm-published
```

语义（全部在一个 DB 事务内）：

1. `SELECT ... FOR UPDATE` 锁定任务；
2. 校验：`status IN ('needs_review','failed')`、无活跃 attempt、`stage='done'`；否则 409 `TASK_ACTION_NOT_ALLOWED`；
3. 幂等：若已有该任务的 `confirm_published/succeeded` 审计行，直接返回现状（200 + 已有确认信息），不重复变更；
4. 插入 `video_publish_confirmations`（操作人=认证主体，时间=数据库 now()）；
5. 更新任务：`status=succeeded`、`confirmed_at/confirmed_by_*`、清错误、`row_version+1`；
6. 唯一索引冲突视为并发确认：回滚后重读，返回已确认事实，不覆盖首笔人/时间。

按钮点击不操作手机、不消耗发布执行、不触发任何 MinIO 动作。已确认成功的任务不提供撤销/改回入口；确认错了由用户重新创建任务（新业务任务），原单记录保留。

## 8. Prompt 契约

Prompt 由两部分拼接，最终文本快照写入 attempt：

```text
<正文：默认模板或用户提交前编辑的 prompt_override>
<强制安全段：服务端固定追加，用户不可编辑、不可移除>
```

**强制安全段**（版本化于 `tts_erp_v2/publishing/prompt.py`）必须包含：

- 使用该手机**当前已登录**的 TikTok 账号发布，不切换或选择账号；
- **先检查 TikTok 是否已登录；未登录直接报告失败并说明原因，不继续上传或点击发布，不尝试自动登录**；
- 只选择一个指定视频，找不到时停止，不能选择其他视频；
- 最终发布按钮最多点击一次；
- 不修改账号设置、不离开锁定 App；
- 成功与失败的可观察条件。

正文包含 App/package、TTSERP 相册与精确设备文件名、文案 JSON 字符串（声明为数据而非指令，保留换行/emoji/标签）。用户编辑正文后服务端仍追加同一强制安全段，因此“未登录直接失败”在任何实际提交的 Prompt 中都必然存在。

核验 Prompt 固定不可编辑，必须明确禁止任何发布动作，包含 caption、源对象身份、预期最新发布时间窗与缩略图比对指令；证据不足返回 `inconclusive`。

## 9. HTTP API

所有浏览器写请求发送 `X-Requested-With: tts-erp`。**授权模型**：登录 + `page:video-publish` 即可读全部任务并执行全部合法操作；不再按创建人过滤或区分读写角色；未授权时页面与接口都拒绝。

### 9.1 页面启动配置

```http
GET /v2/video-publish/config
```

```json
{
  "acceptedContentTypes": ["video/mp4"],
  "maxVideoBytes": 524288000,
  "maxCaptionCharacters": 4000,
  "uploadUrlTtlSeconds": 900,
  "refreshIntervalSeconds": 5,
  "defaultTarget": {
    "appName": "TikTok",
    "appPackage": "com.zhiliaoapp.musically",
    "deviceSerialMasked": "D123…00AC",
    "album": "TTSERP"
  },
  "defaultPrompt": "<默认正文模板>",
  "mandatorySafetyClause": "<强制安全段文本（只读展示）>",
  "worker": {"status": "ready", "lastHeartbeatAt": "2026-10-12T10:42:14Z"},
  "devices": [
    {"serialMasked": "D123…00AC", "status": "ready", "message": "设备在线且空闲"}
  ],
  "serverTime": "2026-10-12T10:42:15Z"
}
```

### 9.2 创建任务与上传票据 / 9.3 刷新票据 / 9.4 确认入队

与既有契约相同：`POST /tasks`（`clientRequestId` 幂等）、`POST /tasks/{id}/upload-url`（仅 `awaiting_upload`）、`POST /tasks/{id}/confirm-upload`（HEAD 校验后 `pending/queued`）。创建请求可带 `prompt`、`deviceSerial`、`appPackage` 初始值；缺省用服务端默认。

### 9.5 提交前参数编辑

```http
PATCH /v2/video-publish/tasks/{task_id}/settings
{"rowVersion": 3, "prompt": "...", "deviceSerial": "...", "appPackage": "..."}
```

仅当 `params_locked_at IS NULL` 且任务处于 pending 阶段时接受；校验 serial/package 格式与 Prompt 长度。任务已 running 返回 409 `TASK_BUSY`（可等本轮结束或取消后再改）；已锁定返回 409 `TASK_ALREADY_SUBMITTED`。视频与文案不可通过此接口修改。强制安全段不在编辑范围。

### 9.6 查询

```http
GET /v2/video-publish/tasks/current
GET /v2/video-publish/tasks?status=&device=&limit=30&cursor=
GET /v2/video-publish/tasks/{task_id}
```

`/current` 返回各设备轨道上的运行任务数组（多设备并行，不再是单一任务）、服务端时间；无运行任务返回 `tasks: []`。列表支持状态与设备筛选，返回摘要与本设备队列位置；详情返回任务、参数快照与锁定状态、清理状态、全部 attempts 与确认审计。队列位置按**目标设备**计算（与该设备真实可领取顺序一致）。三个 GET 支持 `ETag`/`If-None-Match`；ETag 与 payload 由同一缓存条目持有，无 payload 不得接受 304 作为可渲染结果。

详情中的确认信息：

```json
{
  "confirmation": {
    "confirmedBy": "user:42",
    "confirmedAt": "2026-10-12T11:03:20Z",
    "fromStatus": "needs_review"
  },
  "paramsLockedAt": "2026-10-12T10:46:31Z"
}
```

旧自动成功任务返回 `confirmation: null` 与来源说明，不伪造。

### 9.7 操作

```http
POST /v2/video-publish/tasks/{task_id}/cancel             # 仅未提交，见 §6.5
POST /v2/video-publish/tasks/{task_id}/retry              # failed，无限次手动
POST /v2/video-publish/tasks/{task_id}/verify             # needs_review，只读核验
POST /v2/video-publish/tasks/{task_id}/confirm-published  # 唯一成功路径，见 §7.4
POST /v2/video-publish/tasks/{task_id}/cleanup/retry      # 仅 device/spool
PATCH /v2/video-publish/tasks/{task_id}/settings          # 提交前编辑，见 §9.5
```

非法状态统一 409 并附 `allowedActions`。不存在任何 stop/terminate 端点。

### 9.8 稳定错误码（增量）

| code | HTTP | 场景 |
| --- | --- | --- |
| `TASK_ALREADY_SUBMITTED` | 409 | 已 admission 后尝试取消/停止/改参数。 |
| `TASK_SUBMITTING` | 409 | admission 进行中，取消需稍后按真实状态重试。 |
| `TASK_BUSY` | 409 | running 阶段尝试编辑参数。 |
| `TIKTOK_NOT_LOGGED_IN` | 失败落库 | 执行发现未登录，任务直接 failed。 |
| `CONFIRM_NOT_ALLOWED` | 409 | 当前状态/活跃执行不允许确认（并入 `TASK_ACTION_NOT_ALLOWED` 亦可）。 |

移除 `RETRY_BUDGET_EXHAUSTED`（无预算概念）；其余错误码沿用既有契约。

## 10. 模块与文件布局

```text
tts_erp_v2/publishing/
├── __init__.py              # 窄 public interface
├── domain.py                # 状态、命令、outcome、转换规则（含 HUMAN_CONFIRM）
├── submission.py            # 创建票据、confirm、cancel、settings 编辑
├── confirmation.py          # confirm_published：状态+审计同事务
├── dispatcher.py            # dispatch_one(device) / recover_active
├── repository.py            # 按设备 claim 与持久化转换
├── prompt.py                # 正文模板/override + 强制安全段拼接
├── artemis_client.py        # 仓库内 httpx Artemis adapter
├── adb_device.py            # 受控 ADB adapter
├── object_store.py          # video bucket adapter（无删除路径）
├── diagnostics.py           # 有界递归诊断清洗
├── observability.py         # 受控结构化事件
├── safe_values.py           # 共享 serial 掩码
└── worker.py                # 独立进程入口（按设备执行槽）
```

公共业务入口：

```python
create_upload_ticket(...)
confirm_upload(...)
update_settings(...)      # 提交前参数编辑
cancel_task(...)          # 仅未提交
retry_task(...)           # failed 无限手动
request_verification(...) # needs_review 只读核验
confirm_published(...)    # 人工确认成功 + 审计
dispatch_one(device_serial)
recover_active(...)
```

FastAPI handler 只做 wire 校验、页面授权和 outcome → HTTP 映射。

## 11. 配置

```env
TIKTOK_PUBLISH_MINIO_BUCKET=tiktok-video
ARTEMIS_BASE_URL=http://127.0.0.1:8001
ARTEMIS_DEVICE_SERIAL=D123084100AC        # 默认设备；可选多设备来自 Artemis /api/devices
ARTEMIS_APP_PACKAGE=com.zhiliaoapp.musically
ARTEMIS_PROFILE=pro
ARTEMIS_VERIFICATION_LEVEL=strict
TIKTOK_PUBLISH_ALBUM=TTSERP
TTS_ERP_PUBLISH_SPOOL_DIR=~/.local/share/tts-erp/video-publish
TIKTOK_PUBLISH_MAX_VIDEO_BYTES=524288000
```

移除 `TIKTOK_PUBLISH_MAX_ATTEMPTS`（无次数上限）。不新增任何 retention/过期配置。凭据只来自环境或既有 secret 管理。

## 12. 故障恢复

Worker 启动 `recover_active()`：

1. 找到所有 `status=running` 任务（每台设备至多一个）；
2. 已有 Artemis session 的先查同一 session，不重建；
3. session 运行中恢复轮询；已终止按结果推进（成功/不确定 → `needs_review`，明确失败 → `failed`）；
4. 尚未 admission 且 `cancel_requested`：安全收尾后落 `cancelled/done`；
5. 尚未 admission 且无取消请求：按 stage 检查 spool/设备文件后继续或安全回退到 `failed/done`；
6. 设备/spool 清理独立重试；设备清理失败只阻塞该设备下一次领取。

进程被终止、HTTP 超时或数据库短暂不可用，都不能通过“新建另一个业务任务”恢复；已提交的 Artemis session 永远不被停止。

## 13. 核心决策汇总

1. ttsERP 管理业务任务；Artemis 只执行手机操作。
2. 唯一成功路径是人工 `confirm-published`；AI/核验结果只进入 `needs_review`。
3. 首次 Artemis admission（`params_locked_at`）是取消/编辑的永久分界；之后无取消、无停止、无参数修改。
4. 失败不自动重试；人工重试不限次数、不复制业务任务。
5. MinIO 原视频任何状态都不删除；清理只剩设备/spool 临时副本。
6. 互斥粒度是设备：每台设备一个 running，跨设备并行；队列位置按设备计算。
7. 页面授权是唯一人员门槛；指标与查询为页面级全局口径。
8. Prompt = 可编辑正文 + 服务端强制安全段；未登录失败条款不可移除。
9. 同一 attempt 的网络重试复用 session ID；人工新一轮才生成新 session ID。
10. 超时继续查询，不判失败；查询超时不等于本轮结束。
11. 确认审计与状态变更同事务；第一笔确认由唯一约束保证不被覆盖。
12. 每条 Artemis attempt 的完整 session ID 保留可复制；不得把 ttsERP task ID 冒充 Artemis ID。

## 14. 实现级数据字典（增量与修订）

沿用既有字段定义，以下为本稿变更项。

### 14.1 `video_publish_tasks` 变更字段

| 字段 | 写入方 | 含义与约束 |
| --- | --- | --- |
| `prompt_override` | API（提交前） | 用户编辑的 Prompt 正文；为空用默认正文；锁定后拒绝写入。 |
| `params_locked_at` | Worker（admission 成功事务） | 首次成功提交时间；非空后取消/停止/参数编辑一律拒绝。 |
| `confirmed_at` | confirmation 服务 | 数据库接收确认时间，等于页面展示的点击时间。 |
| `confirmed_by_user_id` / `confirmed_by_key_hash` | confirmation 服务 | 认证主体；客户端不可传。 |
| `cancel_requested` | API | 仅 admission 前可置位；Worker 阶段边界检查。 |
| `last_verify_verdict` | Worker | 最近一次核验结论线索；不改变状态。 |
| `attempt_count` | Domain service | 追加式审计计数；**不作重试门槛**。 |
| `publish_budget_used` | — | **退役**：不再写入/读取；旧列保留兼容。 |
| `object_cleanup_*` | — | **退役**：不调度对象删除；API 不暴露。 |

### 14.2 `TaskStatus`（修订含义）

| 值 | 中文标签 | 含义 |
| --- | --- | --- |
| `pending` | 待处理 | 待上传、排队或等待本设备。 |
| `running` | 执行中 | 已占本设备执行槽，正在下载/staging/调用/核验。 |
| `succeeded` | 已发布 | **人员点击确认且审计已保存**；无确认审计的旧成功任务如实标注来源。 |
| `failed` | 发布失败 | 本轮明确失败（含未登录）；可无限次手动重试。 |
| `needs_review` | 结果待确认 | 报告成功或不确定；等待确认按钮或只读核验。 |
| `cancelled` | 已取消 | 从未成功提交；MinIO 原视频仍保留。 |

### 14.3 `AllowedAction`（修订）

| 值 | 按钮文案 | 允许场景 |
| --- | --- | --- |
| `view` | 查看详情 | 所有任务。 |
| `continue_upload` | 继续上传 | awaiting_upload。 |
| `edit_settings` | 修改发布设置 | 未锁定且 pending 阶段。 |
| `cancel` | 取消任务 | 未锁定（pending；running 未提交时经 cancel_requested）。 |
| `retry` | 重试原任务 | failed、无活跃 attempt、对象有效；无次数限制。 |
| `verify` | 只读辅助核验 | needs_review。 |
| `confirm_published` | 确认已发布 | needs_review/failed、无活跃 attempt。 |
| `retry_cleanup` | 重试清理 | device/spool cleanup=failed。 |
| `copy_artemis_id` | 复制 Artemis ID | 至少一个 attempt。 |

### 14.4 `FailureClassification`（诊断用，替代 RetryClassification）

保留分类值用于详情展示（`pre_artemis_failure/admission_rejected/planner_zero_steps/failed_before_publish_ui/publish_action_observed/session_missing/tiktok_not_logged_in/...`），**不驱动任何自动重排或核验**；删除 `retry_safe` 与 `retry_budget_exhausted` 语义。

## 15. 合法状态转换（修订）

| 当前 | 事件 | 下一状态 | 事务内副作用 |
| --- | --- | --- | --- |
| pending/awaiting_upload | `UPLOAD_CONFIRMED` | pending/queued | 保存 ETag、uploaded_at、queued_at。 |
| 未锁定 pending | `USER_CANCEL` | cancelled/done | MinIO 保留；无对象删除。 |
| 未锁定 running（未 admission） | `USER_CANCEL` | running（置 cancel_requested）→ 收尾后 cancelled/done | 阶段边界停止，禁止提交。 |
| pending/queued | `CLAIM(device)` | running/downloading | 写 lease、heartbeat、started_at。 |
| pending/queued | `DEVICE_NOT_READY` | pending/waiting_device | 本设备退避，不影响他设备。 |
| running/staging_device | `MEDIA_VISIBLE` | running/dispatching_artemis | 写 device_path、device cleanup pending。 |
| running/dispatching_artemis | `ARTEMIS_ADMITTED` | running/waiting_artemis | **写 params_locked_at**。 |
| running/waiting_artemis | `ARTEMIS_SUCCESS` | **needs_review/done** | 释放 lease；等待人工确认。 |
| running/waiting_artemis | `ARTEMIS_FAILURE`（含未登录） | failed/done | 写错误与分类；不自动重排。 |
| running/waiting_artemis | `AMBIGUOUS_FAILURE` | needs_review/done | 记录不确定原因；不自动核验。 |
| needs_review | `USER_VERIFY` | running/verifying → 完成后 needs_review/done | verify attempt；写 last_verify_verdict。 |
| needs_review / failed | `HUMAN_CONFIRM` | succeeded/done | 插入确认审计 + 写 confirmed_*（§7.4）。 |
| failed | `USER_RETRY` | pending/queued | 清错误/completed_at；无次数校验。 |

不变量：活跃 attempt 唯一；`kind=verify` 必须有 `related_attempt_id`；`succeeded` 后禁止新 publish attempt；锁定后禁止参数写入；所有转换、attempt、审计同事务提交。

## 16. 后端核心逻辑（增量）

### 16.1 按设备领取

```python
def claim_one(session, instance_id, device_serial):
    if has_pending_device_cleanup(session, device_serial):
        return None
    row = select_for_update_skip_locked(
        status="pending", stage_in=["queued", "waiting_device"],
        device_serial=device_serial,
        due=True, order_by="queued_at, id", limit=1,
    )
    ...
```

### 16.2 人工确认

```python
def confirm_published(session, task_id, actor, request_id, row_version):
    row = select_for_update(task_id)
    require_page_access(actor)
    if existing_confirmation(task_id):
        return snapshot(row)  # 幂等重放，不覆盖
    require(row.status in ("needs_review", "failed") and row.stage == "done")
    require_no_active_attempt(task_id)
    insert_confirmation(task_id, actor, request_id,
                        from_status=row.status, to_status="succeeded")
    update_where_version(task_id, row_version,
                         status="succeeded", confirmed_at=db_now(),
                         confirmed_by_user_id=actor.user_id, ...)
    commit()
```

### 16.3 提交前编辑

```python
def update_settings(session, task_id, actor, cmd, row_version):
    row = select_for_update(task_id)
    require_page_access(actor)
    require(row.params_locked_at is None)            # 否则 TASK_ALREADY_SUBMITTED
    require(row.status == "pending")                  # running → TASK_BUSY
    apply_validated(cmd)  # prompt 正文长度、serial/package 格式
    update_where_version(..., row_version=row_version + 1)
```

### 16.4 未提交取消

pending 直接 `cancelled/done`；running 未 admission 置 `cancel_requested`，Worker 在阶段边界检查后安全收尾；`params_locked_at` 非空一律 409。

## 17. 完整 HTTP 契约示例（增量）

### 17.1 确认已发布

```http
POST /v2/video-publish/tasks/38f1.../confirm-published
Content-Type: application/json
X-Requested-With: tts-erp

{"rowVersion": 12, "requestId": "c0ffee-..."}
```

```json
{
  "taskId": "38f1...",
  "status": "succeeded",
  "confirmation": {
    "confirmedBy": "user:42",
    "confirmedAt": "2026-10-12T11:03:20Z",
    "fromStatus": "needs_review"
  },
  "allowedActions": ["view", "copy_artemis_id"],
  "rowVersion": 13
}
```

重复请求返回相同内容与 200；并发第二笔得到相同已确认事实。已锁定任务重试中（running）调用返回 409。

### 17.2 当前任务（多设备）

```json
{
  "tasks": [
    {"taskId": "38f1...", "deviceSerialMasked": "D123…00AC", "status": "running",
     "stage": "waiting_artemis", "currentAttempt": {"artemisSessionId": "c8af..."}},
    {"taskId": "9ab2...", "deviceSerialMasked": "E456…11BD", "status": "running",
     "stage": "staging_device", "currentAttempt": null}
  ],
  "serverTime": "2026-10-12T10:46:15Z"
}
```

### 17.3 详情中的保存区

```json
{
  "object": {"bucket": "tiktok-video", "key": "video-publish/...", "etag": "...",
             "retention": "已保留（不删除）"},
  "cleanup": {
    "device": {"status": "succeeded", "error": null},
    "spool": {"status": "succeeded", "error": null}
  }
}
```

## 18. 前端实现规格（修订点）

- **刷新**：固定模式，上一响应完成后计时 5 秒；开关只有 开/关 + “立即刷新”；无 smart 模式。页面隐藏暂停计时，可见且开启时立即刷新。
- **设备轨道**：每台设备一条轨道，显示该设备当前任务与队列；不再单条全局轨道。
- **详情 drawer**：新增“确认”区——唯一按钮 `确认已发布`（`#publish-confirm-published`），成功后原地显示操作人与点击时间；无“确认未发布/无法确定”按钮，无证据表单。
- **设置编辑**：pending 且未锁定显示 Prompt/App/设备编辑；锁定后只读并展示锁定时间。
- **取消**：仅未锁定显示；锁定后入口消失，接口同样拒绝。
- **重试确认 dialog**：不再显示次数/上限；提示“AI 报告失败可能有误，请先自行核验作品页再决定是否重试”。
- **清理区**：只显示 device/spool；MinIO 固定显示“已保留（不删除）”。

## 19. 日志、监控与运维（修订点）

- 新增事件 `publish_confirmed`（task_id、operator 掩码、from_status）；禁止记录 caption/Prompt/secret。
- 指标为**页面级全局口径**：`queueDepth{device}`、`running{device}`、`needsReview`、`cleanup{resource=device|spool}`、`workerHeartbeatAgeSeconds`；不再 owner-scoped。
- 告警：`needs_review > 0 持续 30 分钟` 提醒人工确认；`cancel_requested` 超过 2 分钟未落地检查 Worker。

## 20. 安全与数据保留（修订）

- 全部接口校验登录 + `page:video-publish` + `X-Requested-With`；不按创建人过滤；凭据永不入响应/日志。
- MinIO bucket 私有，对象永不删除；GET 预览 URL 短期生成。
- ADB 不接受任意 shell；Artemis base URL 固定。
- **任务、attempt、确认审计暂不设置自动删除**；不配置任何到期回收任务。删除 API 不提供。
- 设备 serial 在列表/详情/指标中掩码；设备选择面与 `/config` 同属页面授权面。

## 21. 与当前实现的差距（如实记录）

当前代码仍是旧规则实现，以下差距是实施待办，**不影响本文作为目标设计**：

1. `commit_publish_transition()` 目前把执行 success / verify `published` 直接置 `succeeded` 并调度对象删除；目标改为 `needs_review` + 人工确认，对象不删。
2. 当前唯一索引是全局单 running；目标改为按设备唯一并支持跨设备并行领取。
3. 当前存在 `publish_budget_used` / `TIKTOK_PUBLISH_MAX_ATTEMPTS` 与自动重排；目标删除预算与自动重试。
4. 当前取消仅支持未运行任务；目标扩展到“未提交”的 running 准备阶段，且提交后全面禁止。
5. 当前查询/指标 owner-scoped；目标为页面级统一口径。
6. 当前无 `video_publish_confirmations` 表与 `confirm-published` 接口；需新迁移与实现。
7. 当前 Prompt 固定不可编辑；目标为可编辑正文 + 强制安全段。

迁移与切换顺序见[实施计划](03-implementation-plan.md)；旧任务记录不批量改写、不伪造确认人与时间。
