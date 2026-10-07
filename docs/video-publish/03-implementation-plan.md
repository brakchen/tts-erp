# TikTok 视频发布：实施计划

> **文档导航**：[产品方案](01-product-proposal.md) · [技术设计](02-technical-design.md) · [实施计划](03-implementation-plan.md) · [测试计划和结果](04-test-plan-and-results.md)

> 来源基线：`docs/video-publish-design@b88b26c` 中的 `docs/design/tiktok-video-publish.md`。
> 本目录按职责拆分原 2781 行单体文档；四个文件共同构成当前项目文档集。

## 文档职责

本文只管理实施顺序、任务边界、交付物、依赖、部署与回滚。不得用“顺手修”扩大批次；每个批次都必须对应一个 canonical owner 和独立验收证据。

## 当前修复实施顺序

### Phase 0：恢复可控基线

- 所有开发转入独立 lane/worktree，停止在脏 master 上叠加改动。
- 统一问题台账：每条问题只能有一个优先级、一个 owner、一个验收测试。
- 修复 `scripts/test_isolated.sh publishing` 的收集入口，并把临时 `/tmp` 场景迁入仓库。
- 交付门槛：测试入口真实收集用例；P1/P2 都有明确 PASS/FAIL 证据。

### Phase 1：后端领域契约

- 决定 retry 与 device/spool cleanup 的状态语义。
- 让领域层统一拥有 allowed actions 与写路径资格判定。
- 收口 current 优先级、owner-scoped poll state、queue eligibility 和统一错误信封。
- API 层只做认证、协议转换与领域错误映射，不承担第二套业务状态机。

### Phase 2：前端状态与缓存

- 拆分 request/cache、list、detail、upload 四个状态 owner。
- ETag 与 payload 绑定；筛选、分页、drawer reopen 分别有稳定缓存语义。
- 上传失败通过 `/tasks/{id}/upload-url` 刷新票据，不再借重复 POST 隐式续传。
- 文件类型、大小、文案上限全部读取服务端配置。

### Phase 3：UI、响应式与可访问性

- 收口错误态、空态、设备状态、状态枚举、焦点恢复和 aria。
- `<720px` drawer 真正全屏；关键断点做视觉回归。
- 不再通过颜色单独表达状态。

### Phase 4：回归与发布门禁

- 运行 publishing、fast、浏览器多视口和认证 HTTP 契约。
- P1/P2 不允许保留“未验证”；必需测试不得依赖 skip。
- 真机发布仍需用户显式确认；普通回归不得向真实 TikTok 发布。

## 交付批次模板

每个批次必须记录：

- 问题/需求 ID；
- canonical owner 与改动文件；
- RED 证据；
- 最小实现边界；
- GREEN 与回归命令；
- 未验证的外部依赖；
- 分支、commit 与同步的 master；
- 是否改变 API、字段、枚举或状态转换。

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
## 23. 基本部署方案

### 23.1 拓扑

```text
Browser
  ├─ HTTPS /tts/* ─────────────→ NGINX → tts-erp API :9877
  └─ HTTPS /minio/* signed PUT → MinIO :9000

tts-erp API / publish worker
  ├─ PostgreSQL
  ├─ MinIO internal endpoint
  ├─ Artemis http://127.0.0.1:8001
  └─ adb server → D123084100AC
```

Artemis 和 ADB 不直接暴露给浏览器或公网。若 Artemis 不在同机，使用受限内网、token 和防火墙 allowlist。

### 23.2 PostgreSQL migration

migration 必须：

1. `CREATE SCHEMA IF NOT EXISTS publishing`；
2. 创建三张表和所有 CHECK/FK/unique/index；
3. 安装 `updated_at` trigger，复用项目现有 trigger helper；
4. 创建部分唯一索引；
5. 不读取或修改生产业务数据；
6. downgrade 只允许在确认没有任务数据时执行，否则拒绝或由运维先导出。

关键索引：

```sql
CREATE INDEX ix_video_publish_queue
ON publishing.video_publish_tasks (next_attempt_at, queued_at, id)
WHERE status = 'pending' AND stage IN ('queued', 'waiting_device');

CREATE INDEX ix_video_publish_history
ON publishing.video_publish_tasks (created_at DESC, id DESC);

CREATE INDEX ix_video_publish_attempt_task_seq
ON publishing.video_publish_attempts (task_id, sequence_no DESC);

CREATE INDEX ix_video_publish_attempt_status
ON publishing.video_publish_attempts (status, updated_at)
WHERE status IN ('created', 'submitting', 'queued', 'running', 'unknown');
```

### 23.3 MinIO bucket

创建私有 bucket `tiktok-video`。禁止 anonymous read/write。服务端账号至少需要：

```text
s3:GetObject
s3:PutObject
s3:DeleteObject
s3:HeadObject
```

限制到 bucket/prefix `video-publish/*`。浏览器通过预签名 URL 上传，不获得 access key。

CORS 示例（origin 按实际域名替换）：

```xml
<CORSConfiguration>
  <CORSRule>
    <AllowedOrigin>https://erp.example.com</AllowedOrigin>
    <AllowedMethod>PUT</AllowedMethod>
    <AllowedMethod>HEAD</AllowedMethod>
    <AllowedHeader>content-type</AllowedHeader>
    <AllowedHeader>content-length</AllowedHeader>
    <ExposeHeader>etag</ExposeHeader>
    <MaxAgeSeconds>3600</MaxAgeSeconds>
  </CORSRule>
</CORSConfiguration>
```

生产不要设置 bucket lifecycle 自动删除成功前对象；清理由业务 Worker 控制。可以另外设置临时 multipart 残片清理策略。

### 23.4 本地 spool

默认：

```text
~/.local/share/tts-erp/video-publish/
```

要求：

- 目录 owner 为 publish service 用户，权限 `0700`；
- 文件 `0600`；
- 每次 execution 的目录 `<task_public_id>/<execution_generation>/video.mp4.part|video.mp4`，设备文件同样使用唯一 `tts_erp_<execution_generation>.mp4`；
- 先写 `.part`，fsync 后原子 rename；登记的 `spool_path` 同时拥有该精确 final 与精确 sibling `.part`，process kill 后 selector 必须幂等删除并验证两者；
- spool 所在磁盘预留至少 `2 × maxVideoBytes + safety margin`；
- 日志不打印完整本地路径中的原始用户文件名。

启动时扫描 orphan spool：只对 DB 中证明已终态且超过保留窗的任务原子调度/重开 spool cleanup；扫描器不删除目录，实际删除仍由 cleanup selector 独占。未知或活跃目录保留，禁止 `rm -rf` 整个根目录。

### 23.5 ADB 与手机

- systemd 用户必须能调用固定 `adb` binary；
- 设备通过 USB debugging 或受控 ADB TCP 接入；
- 明确配置 `ARTEMIS_DEVICE_SERIAL`，禁止依赖“第一台设备”；
- 手机保持解锁、TikTok 已登录、系统相册权限已授权；
- `/sdcard/Movies/TTSERP/` 只放系统管理的 `tts_erp_<uuid>.mp4`；
- 不授予 Worker 任意删除其他相册目录的能力；
- udev/USB 权限由运维配置，不以 root 运行整个 ttsERP。

### 23.6 Artemis

发布 Worker 使用仓库内 `tts_erp_v2/publishing/artemis_client.py` 的 `httpx` adapter；不要求另装或固定不存在的外部 `artemis-client` 包。配置：

```env
ARTEMIS_BASE_URL=http://127.0.0.1:8001
ARTEMIS_TOKEN=<secret-if-enabled>
ARTEMIS_DEVICE_SERIAL=D123084100AC
ARTEMIS_APP_PACKAGE=com.zhiliaoapp.musically
ARTEMIS_PROFILE=pro
ARTEMIS_VERIFICATION_LEVEL=strict
```

部署前 smoke check：

```text
GET /healthz 或 SDK health()
GET /api/devices 包含精确 serial
GET /api/system/readiness 无 fatal
```

不得用真实发布 Prompt 做健康检查。

### 23.7 systemd unit

检查并安装仓库中的 `scripts/systemd/tts-erp-publish.service` user unit；其内容与实际部署路径一致：

```ini
[Unit]
Description=ttsERP TikTok publish worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/tts-erp
EnvironmentFile=%h/tts-erp/.env
ExecStart=%h/tts-erp/.venv/bin/python -m tts_erp_v2.publishing.worker
Restart=always
RestartSec=5
TimeoutStopSec=30
KillSignal=SIGTERM
NoNewPrivileges=true
PrivateTmp=true
UMask=0077

[Install]
WantedBy=default.target
```

该 unit 以当前用户运行，以便继承其 USB/ADB 权限；确保 `PrivateDevices` 等 hardening 不阻断 USB/ADB。缺少 `ARTEMIS_DEVICE_SERIAL` 时 Worker 可启动但设备 readiness 为 `unknown`，API 创建任务会失败关闭；缺少 `ARTEMIS_BASE_URL` 或 `TTS_ERP_PUBLISH_SPOOL_DIR` 时 Worker 启动失败。先在 staging 主机验证再收紧 sandbox。

常用命令：

```bash
systemctl --user daemon-reload
systemctl --user enable --now tts-erp-publish.service
systemctl --user status tts-erp-publish.service
journalctl --user -u tts-erp-publish.service -f
```

### 23.8 NGINX 与路径前缀

新 API 和页面随既有 `/tts/` 反代，无需暴露 Worker 端口。必须验证：

- `/tts/v2/pages/video-publish` 可打开；
- 相对静态资源 `/tts/static/...` 正常；
- JS 从 pathname 推导 `/tts/v2`；
- MinIO signed URL 使用浏览器可达的 `MINIO_PUBLIC_HOST`；
- NGINX/API 不代理大视频 body，因为浏览器直传 MinIO。

### 23.9 环境变量字典

| 变量 | 必填 | 默认 | 含义 |
| --- | --- | --- | --- |
| `TIKTOK_PUBLISH_MINIO_BUCKET` | 是 | 无 | 私有视频 bucket。 |
| `TIKTOK_PUBLISH_MAX_VIDEO_BYTES` | 否 | 524288000 | 视频上限。 |
| `TIKTOK_PUBLISH_MAX_CAPTION_CHARS` | 否 | 4000 | 文案字符上限。 |
| `TIKTOK_PUBLISH_UPLOAD_TTL_SECONDS` | 否 | 900 | 预签名 PUT 有效期。 |
| `TIKTOK_PUBLISH_FAILED_RETENTION_DAYS` | 否 | 30 | 失败媒资保留期。 |
| `TIKTOK_PUBLISH_MAX_ATTEMPTS` | 否 | 3 | 正式发布 attempt 上限。 |
| `TIKTOK_PUBLISH_ALBUM` | 否 | TTSERP | 设备相册/目录名。 |
| `TTS_ERP_PUBLISH_SPOOL_DIR` | 是 | 无 | 本地 spool 根目录。 |
| `ARTEMIS_BASE_URL` | 是 | 无 | Artemis daemon URL。 |
| `ARTEMIS_TOKEN` | 视部署 | 无 | Artemis 鉴权 token。 |
| `ARTEMIS_DEVICE_SERIAL` | API 创建任务必需 | 无 | 唯一目标设备；缺失时 Worker 仍启动但 readiness=`unknown`，API 创建失败关闭。 |
| `ARTEMIS_APP_PACKAGE` | 否 | com.zhiliaoapp.musically | TikTok package。 |
| `ARTEMIS_PROFILE` | 否 | pro | 执行 profile。 |
| `ARTEMIS_VERIFICATION_LEVEL` | 否 | strict | Pro checker 强度。 |
| `PUBLISH_POLL_INTERVAL_SECONDS` | 否 | 2 | 后端查 Artemis 间隔。 |
| `PUBLISH_TASK_LEASE_SECONDS` | 否 | 30 | DB task lease。 |
| `PUBLISH_WORKER_HEARTBEAT_SECONDS` | 否 | 5 | Worker 心跳周期。 |

Worker 启动直接要求 `ARTEMIS_BASE_URL` 与 `TTS_ERP_PUBLISH_SPOOL_DIR`，缺少任一项即失败；bucket 有受校验的 `tiktok-video` 默认值。缺少 `ARTEMIS_DEVICE_SERIAL` 时 Worker 仍可启动，设备 readiness=`unknown`，API 创建任务失败关闭；不得静默选择“第一台设备”。

### 23.10 部署顺序

1. 发布代码但不启用页面权限和 Worker；
2. 执行 migration；
3. 创建 MinIO bucket/policy/CORS；
4. 创建 spool 并校验权限/磁盘；
5. 安装/验证 Artemis client 和 ADB；
6. 启动 publish worker，确认 heartbeat ready；
7. API smoke：config/create/cancel，使用小型测试 MP4，不调用真实发布；
8. 模拟器 dry-run；
9. 真机 staging-only；
10. 用户显式确认后真实发布一条；
11. 授权 `page:video-publish` 给 operator。

### 23.11 回滚

应用回滚优先级：

1. 从角色移除页面权限或 feature flag，阻止新任务；
2. 停止 Worker 领取新任务；
3. 对 running Artemis session 继续只读查询并保存结果，不盲目 stop；
4. 保留数据库表、MinIO 对象和 attempt 历史；
5. 回滚 API/页面代码；
6. 数据 migration 只有在表为空并经人工确认时 downgrade。

不得以“回滚”为理由删除 needs_review 或 running 任务的媒资。
## 27. GPT Luna 开发交接清单

Luna 开始开发前必须按顺序阅读：

1. 根目录 `AGENTS.md`；
2. `docs/guides/agent-safety.md`；
3. `docs/architecture/architecture-overview.md`；
4. `docs/architecture/process-architecture.md`；
5. `docs/design/ui-style-system.md`；
6. 本方案全文；
7. 现有 `tts_erp_v2/storage/minio_client.py` 与 `api/v2/spu_images.py`；
8. Artemis client 的 `submit/get_task/wait_for_task` 契约。

开发约束：

- 在独立 lane/worktree 开发并登记所有 owned files；
- 测试只用 `bash scripts/test_isolated.sh ...`；
- 先测试后实现，每个 Phase 可独立验收；
- 不把之前外部 PoC 的自定义 SigV4 客户端搬入 ttsERP；
- 不在 FastAPI handler 内执行 ADB、下载大文件或长轮询；
- 不在浏览器直接访问 Artemis；
- 不跳过 DB 状态机直接更新字段；
- 不运行真实发布，除非当前用户再次明确授权；
- 不擅自改变本方案枚举/错误码/按钮语义；如实现发现冲突，先更新方案并说明迁移影响。

建议提交拆分：

```text
1. feat(publishing): add schema and domain state machine
2. feat(publishing): add upload and task APIs
3. feat(publishing): add object, adb, and Artemis adapters
4. feat(publishing): add resilient publish worker
5. feat(ui): add video publish console
6. test(publishing): cover retries, recovery, and UI contracts
7. docs: add deployment and operator runbook
```

Luna 每个 Phase 的输出必须包含：

- 改动文件；
- 新增/修改的 public interface；
- migration head；
- 执行过的隔离测试及结果；
- 未验证的真实设备风险；
- 是否改变本方案中的 API、字段或枚举；
- 当前分支/commit，且未经 review 不合并 master。
