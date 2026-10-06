# TikTok 视频发布运维手册

## 边界

浏览器只访问 ttsERP；视频通过短期预签名 PUT 直传私有 MinIO。Worker 是唯一可访问 ADB/Artemis 的进程，数据库保留 bucket/key/ETag 和每个 Artemis session。禁止用真实任务做健康检查。

## 部署顺序

1. 使用测试形数据库验证 video-publish migrations `0053`–`0066`，再由运维执行生产迁移。规范命令为 `bash scripts/test_isolated.sh --refresh-template fast tests/publishing`；禁止直接运行 pytest、禁止将测试指向生产形数据库，也不得把“因 schema 缺失而全部 skipped”当作迁移证据。PostgreSQL 18 服务端必须使用不早于服务端主版本的 `pg_dump`；仓库测试工具默认以 `PG_DOCKER=postgres` 直接使用服务端容器内客户端，因此规范命令无需临时 PATH shim。只有显式设置 `PG_DOCKER=` 时才使用宿主机客户端。
   `0058` 会在加约束前把旧的 pending/queued、pending/waiting_device 清理状态
   回填为显式 cleanup intent；含对象残留的歧义任务转为
   `needs_review/done + preserve_state` 并取消对象删除；取消后无任何待清理资源的终态行会归一为 `cleanup_intent=none`，以便人工核验。`0059` 回填当前阶段起始时间。
   `0060` 对齐基础索引/唯一约束，并约束 publish/verify 的 `related_attempt_id`
   形状及三个 cleanup status 枚举。`0061` 回填追加式 `attempt_count` 与独立
   `publish_budget_used`，加入同 task/publish related-attempt trigger、DESC attempt
   索引和 Worker 设备实际探测字段。`0062` 将对象 key 的安全文件名与浏览器原 basename 分开，并通过 trigger 固化 attempt 的核心关系身份。`0063` 撤销历史自动 operator 授权并确保 admin 授权存在；operator 只能在最终人工验证后手工授权。`0064` 持久化下载前 spool ownership；`0065` 加入每次上传 generation、PUT ticket expiry、attempt app-package 快照；`0066` 加入 execution generation 与 attempt profile/verification 快照。升级会明确拒绝任何 running task、`created|submitting|queued|running|unknown` active attempt、非空 legacy `spool_path/device_path`，以及无法安全重开的非终态 deleted generation；这些状态没有可证明的 execution/Artemis 历史身份，禁止猜测或默认回填。终态 legacy attempt 的 profile/verification 保持诚实 `NULL`，不能恢复为 active 或重提；所有新/active attempt 必须带两个非空精确快照。
   生产执行前停止 API 新票据签发和 Worker 任务领取、确认 Worker 心跳停止，并在 0065 schema 上完成 drain：所有 running task 必须到终态，所有 active attempt 必须取得终态，所有 legacy spool/device 精确路径必须由旧版 owner 清理并置空。然后才可执行迁移。`0066` 按 MinIO/SigV4 的 7 天最大票据 TTL 隔离所有 expiry 未知 generation，包括先前标记 deleted 的行；deleted terminal generation 会清除旧 deletion 结论并重开 generation-bound idempotent object cleanup。selector 再追加 15 分钟 completion grace，以删除可能延迟完成的 PUT。迁移后不得手工提前 `object_cleanup_next_attempt_at`、清空 expiry 或绕过 selector；未知旧票据必须完整保持至少 7 天 + 15 分钟 hold，期间 replacement 继续受 cleanup gate 阻止。随后按迁移顺序协调 API/Worker 重启。
2. 创建私有 `tiktok-video` bucket，并限制 `video-publish/*` 的 Get/Put/Delete/Head 权限；配置来源站点 PUT/HEAD CORS。
3. 创建 `TTS_ERP_PUBLISH_SPOOL_DIR`（0700），设置 `ARTEMIS_BASE_URL`、`ARTEMIS_DEVICE_SERIAL`、`ARTEMIS_APP_PACKAGE`、`TIKTOK_PUBLISH_MINIO_BUCKET=tiktok-video` 和 MinIO 凭据。`MINIO_BUCKET` 必须与专用 bucket 相同；未配置设备序列号或 bucket 不匹配时 API 拒绝创建任务。按需覆盖 `ARTEMIS_PROFILE=pro`、`ARTEMIS_VERIFICATION_LEVEL=strict`、`PUBLISH_POLL_INTERVAL_SECONDS=2`、`PUBLISH_TASK_LEASE_SECONDS=30`、`PUBLISH_WORKER_HEARTBEAT_SECONDS=5`、`PUBLISH_BACKGROUND_CLEANUP_BATCH_SIZE=1`、`PUBLISH_BACKGROUND_CLEANUP_INTERVAL_SECONDS=1`、`TIKTOK_PUBLISH_MAX_CAPTION_CHARS=4000` 与 `TIKTOK_PUBLISH_FAILED_RETENTION_DAYS=30`；API config 返回生效的非敏感 Artemis/轮询配置供运维核对。
4. 由运维安装 user unit：`install -m 0644 scripts/systemd/tts-erp-publish.service ~/.config/systemd/user/tts-erp-publish.service`，核对 unit 环境后执行 `systemctl --user daemon-reload` 和 `systemctl --user enable --now tts-erp-publish.service`。这些生产命令不得由开发/测试 agent 执行。生产环境如需调度或执行删除，须由运维在 API 与 publish worker 的服务环境显式设置 `ALLOW_PROD_DESTRUCTIVE=1`：API 用它授权取消/清理重试的删除调度，worker 用它授权 cleanup selector 领取后的设备、spool、MinIO 实际删除。启动 orphan 扫描只调和数据库清理计划，不直接删除文件。缺少 API 授权时 mutation 返回 403；缺少 worker 授权时对象保留并标记 cleanup failed，不会静默删除。启动后确认 `publishing.worker_heartbeats` 在 15 秒内为 ready，并检查综合 readiness 已探测 serial、ADB 解锁、TikTok package、Artemis 与 MinIO；设备 offline/locked/busy 仍允许创建排队任务。
5. 仅用模拟器/fake adapter 做 staging dry-run；真机先只做 MediaStore dry-run。
6. migration 与 `sync-permissions` 只自动给 admin 授权 `page:video-publish`。完成模拟器、staging-only 与首次人工确认验证后，再由 admin 在“用户管理 → 角色”中手工把 `page:video-publish` 加给 operator；后续幂等 `sync-permissions` 不会自动增加或删除这项手工授权。API key 默认仅 admin tier 可调用整个 `/v2/video-publish/*`；不得为 rollout 设置 `TTS_ERP_VIDEO_PUBLISH_ALLOW_READWRITE_API_KEYS=1`。该 feature gate 只保留给未来经独立安全评审、文档和发布批准的显式启用。首次真实发布必须由用户显式确认。

## 故障处理

- Artemis 请求超时：Worker 查询并复用原 `artemis_session_id`，不要手工新建 session。
- confirm 后对象 ETag 已冻结；Worker 使用 `If-Match` 条件下载。`CONFIRMED_OBJECT_REPLACED` / `CONFIRMED_OBJECT_MISSING` 会在 ADB staging 和 attempt 创建前安全失败，禁止绕过后直接发布。
- ADB push 前同时扫描专用相册文件系统与 MediaStore；任意受管 `tts_erp_*.mp4` 残留都会令任务进入 `waiting_device` 且不创建 Artemis attempt。cleanup selector 只删除数据库登记的精确 `device_path`，删除/重扫对应 MediaStore entry 并轮询至该路径不可见；未确认消失时保持 `device_cleanup_status=failed`。禁止通配删除或手工删除未知路径。
- 结果不确定：必须先 verify；一次自动 verify 的 `not_published` 也只会进入 `needs_review/done`，绝不自动重新发布。`needs_review` 禁止普通 retry，只能由用户之后显式再次核验。
- 设备文件、spool、MinIO 是独立清理状态。每次 claim 新执行先持久化唯一 `execution_generation`；下载前登记 `<spool>/<task>/<generation>/video.mp4`（ownership 同时覆盖 exact `.part`），staging 登记 `tts_erp_<generation>.mp4`。cleanup work 快照退休 generation 的精确路径，不用 wildcard；失去 lease 的旧 cleaner 即使外部调用稍后返回也不可能命中新 execution generation。文件尚不存在时 cleanup 也幂等成功。成功任务的清理失败只执行“重试清理”，不能改写业务成功。
- 每次初始上传/replacement 使用全新 `object_generation/object_key`。既有任务 PUT ticket 签发在 task row lock 下串行校验、签名并持久化单调最大 expiry 后才返回。对象 selector 独立要求 PostgreSQL 时间到达 `object_upload_expires_at + 15 minutes`，不能只信 `object_cleanup_next_attempt_at`；missing object 在此之前也保持 pending，屏障后再幂等删除/确认 absence。work 固定退休 generation/key/确认 ETag；stale cleaner 不能删除后续 replacement。
- 设备清理失败时，主循环会阻止后续任务进入 staging 并按退避重试。spool/object 使用独立有界后台 coroutine、短 DB session 和 cleanup lease，即使发布队列持续有任务也继续推进；慢 MinIO 清理不回到 publish-critical path。任务详情的“重试清理”仅用于在无活动清理租约时立即提前触发重试；取消任务的对象清理失败同样保留 cancelled 状态并可恢复。
- 页面智能刷新以 API 返回的未过滤运行/排队摘要决定节奏，不会因切换到成功/失败筛选而放慢待处理任务；awaiting-upload 草稿不计入排队，选择“关闭”时运行轨道显示“自动刷新已暂停”，重新启用后恢复轮询。
- 上传中的“取消上传”只 abort 当前 XHR，不确认任务；草稿保留原 clientRequestId，可通过“继续上传”恢复。
- 视频任务按创建者隔离：会话用户按 user_id、API key 按 key hash 读取和重放自己的任务；普通 readwrite 请求不能枚举或重放他人任务。admin 是明确的全局运维例外，可查看和管理所有任务。
- Worker 重启会从 running task 继续读取原 session；不要删除数据库行或对象。
- `awaiting_upload` 超过 24 小时会自动转为 `cancelled/done` 并进入受保护的对象清理；`failed` 对象按 `TIKTOK_PUBLISH_FAILED_RETENTION_DAYS`（默认 30 天）保留。`needs_review` 对象不参加失败保留期删除，必须先完成人工判定。
- Worker 启动时只扫描 spool 根目录下一层 UUID 目录；数据库证明对应任务已终态且完成超过 24 小时时，扫描器只原子调度/重开 `spool` 清理，随后由 cleanup selector 领取租约并执行删除。扫描器本身从不删除。未知目录、活跃任务目录、有效清理租约目录和无法读取数据库的目录全部保留；禁止手工 `rm -rf` spool 根目录。
- Artemis output/error 在持久化前递归限深、限项、限长并清除 token、Authorization、cookie、secret、凭据和签名 URL 查询参数。日志只记录 task/attempt/stage/session 标识、稳定结果枚举、始终缺至少一个字符的设备掩码和计数，不记录文案、原始文件名、完整本地路径、Prompt、token 或原始 Artemis output。具备页面权限的只读调用方可用 `GET /v2/video-publish/metrics` 查看其可见范围内的 tasks-by-status、attempts-by-kind/status、queue/running/needs-review/cleanup、`currentStageAgeSeconds` 与 Worker heartbeat age 当前 gauge；admin 查看全局任务聚合。已完成 stage 时长只由 `publish_transition.duration_ms` 供外部 histogram 聚合；终态当前年龄不得称为 duration。指标响应不含文案、Prompt、output、完整设备 serial 或凭据。
- 回滚时先移除页面权限、停止领取新任务；保留 running/needs_review 的对象和审计历史。`0054`–`0059` 的 state-bearing downgrade 在 `publishing.video_publish_tasks` 非空时拒绝执行；只有清空三张 publishing 表并经人工确认才允许回退。不得绕过保护直接删除 cleanup scheduling、owner、intent、lease 或 stage timing 列。`0060` 只移除约束/索引元数据；`0061` 含审计/设备状态字段、`0062` 含文件名语义与 attempt identity trigger，publishing 状态非空时拒绝 downgrade。`0063` downgrade 不会自动扩大 operator 权限；`0064` 在任一 `spool_path` 已登记时拒绝丢弃 side-effect ownership；`0065` 在存在 task/attempt 时拒绝丢弃 upload generation；`0066` 在存在 task/attempt 时拒绝丢弃 execution generation、历史 ticket hold 或扩展 attempt identity。它们仍须先在测试形数据库完成 roundtrip。

## 浏览器与无障碍验收

自动化覆盖键盘可操作按钮、焦点回归、独立 AbortController、页面销毁请求取消、reduced-motion 样式、390/768/1440 对应 CSS 布局断点、结构化安全确认和完整 Artemis ID。发布前仍需人工在 390px、768px、1440px 三档浏览器宽度检查无水平页面溢出、drawer 可关闭、表格卡片标签可读、确认内容不被截断；该人工视觉检查不执行真实设备发布。

## 只读检查

```bash
systemctl --user status tts-erp-publish.service
journalctl --user -u tts-erp-publish.service -n 100
```

不要执行真实 TikTok publish 或任意 ADB shell。发布健康检查只能调用 Artemis readiness/devices 只读接口。
