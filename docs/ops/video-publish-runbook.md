# TikTok 视频发布运维手册

## 边界

浏览器只访问 ttsERP；视频通过短期预签名 PUT 直传私有 MinIO。Worker 是唯一可访问 ADB/Artemis 的进程，数据库保留 bucket/key/ETag 和每个 Artemis session。禁止用真实任务做健康检查。

## 部署顺序

1. 使用测试形数据库验证 video-publish migrations `0053`–`0060`，再由运维执行生产迁移。
   `0058` 会在加约束前把旧的 pending/queued、pending/waiting_device 清理状态
   回填为显式 cleanup intent；含对象残留的歧义任务转为
   `needs_review/done + preserve_state` 并取消对象删除。`0059` 回填当前阶段起始时间。
   `0060` 对齐模型索引/唯一约束，并约束 publish/verify 的 `related_attempt_id`
   语义及三个 cleanup status 枚举。上线前必须修复任何不满足约束的历史行。
   生产执行前停止领取新任务、确认 worker 心跳停止，并按迁移顺序协调 API/worker 重启。
2. 创建私有 `tiktok-video` bucket，并限制 `video-publish/*` 的 Get/Put/Delete/Head 权限；配置来源站点 PUT/HEAD CORS。
3. 创建 `TTS_ERP_PUBLISH_SPOOL_DIR`（0700），设置 `ARTEMIS_BASE_URL`、`ARTEMIS_DEVICE_SERIAL`、`ARTEMIS_APP_PACKAGE`、`TIKTOK_PUBLISH_MINIO_BUCKET=tiktok-video` 和 MinIO 凭据。`MINIO_BUCKET` 必须与专用 bucket 相同；未配置设备序列号或 bucket 不匹配时 API 拒绝创建任务。按需覆盖 `ARTEMIS_PROFILE=pro`、`ARTEMIS_VERIFICATION_LEVEL=strict`、`PUBLISH_POLL_INTERVAL_SECONDS=2`、`PUBLISH_TASK_LEASE_SECONDS=30`、`PUBLISH_WORKER_HEARTBEAT_SECONDS=5`、`TIKTOK_PUBLISH_MAX_CAPTION_CHARS=4000` 与 `TIKTOK_PUBLISH_FAILED_RETENTION_DAYS=30`；API config 返回生效的非敏感 Artemis/轮询配置供运维核对。
4. 安装 `scripts/systemd/tts-erp-publish.service`。生产环境如需调度或执行删除，须由运维在 API 与 publish worker 的服务环境显式设置 `ALLOW_PROD_DESTRUCTIVE=1`：API 用它授权取消/清理重试的删除调度，worker 用它授权设备、spool、MinIO 及启动 orphan 扫描的实际删除。缺少 API 授权时 mutation 返回 403；缺少 worker 授权时对象保留并标记 cleanup failed，不会静默删除。启动后确认 `publishing.worker_heartbeats` 在 15 秒内为 ready。
5. 仅用模拟器/fake adapter 做 staging dry-run；真机先只做 MediaStore dry-run。
6. 给 operator/admin 授权 `page:video-publish`。首次真实发布必须由用户显式确认。

## 故障处理

- Artemis 请求超时：Worker 查询并复用原 `artemis_session_id`，不要手工新建 session。
- 结果不确定：必须先 verify；`needs_review` 禁止普通 retry。
- 设备文件、spool、MinIO 是独立清理状态。成功任务的清理失败只执行“重试清理”，不能改写业务成功。
- 设备清理失败时，Worker 会阻止后续任务领取/进入 staging，并按退避自动领取到期的成功任务重试；对象和 spool 清理失败也会独立按退避自动重试，不改变业务成功。任务详情的“重试清理”仅用于在无活动清理租约时立即提前触发重试；取消任务的对象清理失败同样保留 cancelled 状态并可恢复。
- 页面智能刷新以 API 返回的未过滤运行/排队摘要决定节奏，不会因切换到成功/失败筛选而放慢待处理任务；awaiting-upload 草稿不计入排队，选择“关闭”时运行轨道显示“自动刷新已暂停”，重新启用后恢复轮询。
- 上传中的“取消上传”只 abort 当前 XHR，不确认任务；草稿保留原 clientRequestId，可通过“继续上传”恢复。
- 视频任务按创建者隔离：会话用户按 user_id、API key 按 key hash 读取和重放自己的任务；普通 readwrite 请求不能枚举或重放他人任务。admin 是明确的全局运维例外，可查看和管理所有任务。
- Worker 重启会从 running task 继续读取原 session；不要删除数据库行或对象。
- `awaiting_upload` 超过 24 小时会自动转为 `cancelled/done` 并进入受保护的对象清理；`failed` 对象按 `TIKTOK_PUBLISH_FAILED_RETENTION_DAYS`（默认 30 天）保留。`needs_review` 对象不参加失败保留期删除，必须先完成人工判定。
- Worker 启动时只扫描 spool 根目录下一层 UUID 目录；仅当数据库能证明对应任务已终态且完成超过 24 小时时才删除。未知目录、活跃任务目录和无法读取数据库的目录全部保留；禁止手工 `rm -rf` spool 根目录。
- Artemis output/error 在持久化前递归限深、限项、限长并清除 token、Authorization、cookie、secret、凭据和签名 URL 查询参数。日志只记录任务/session 标识、稳定错误码和计数，不记录文案、原始文件名、完整本地路径、Prompt、token 或原始 Artemis output。
- 回滚时先移除页面权限、停止领取新任务；保留 running/needs_review 的对象和审计历史。`0054`–`0059` 的 state-bearing downgrade 在 `publishing.video_publish_tasks` 非空时拒绝执行；只有清空三张 publishing 表并经人工确认才允许回退。不得绕过保护直接删除 cleanup scheduling、owner、intent、lease 或 stage timing 列。`0060` 只移除约束/索引元数据，不删除任务字段，但仍须在测试形数据库先完成 roundtrip。

## 浏览器与无障碍验收

自动化覆盖键盘可操作按钮、焦点回归、独立 AbortController、页面销毁请求取消、reduced-motion 样式、390/768/1440 对应 CSS 布局断点、结构化安全确认和完整 Artemis ID。发布前仍需人工在 390px、768px、1440px 三档浏览器宽度检查无水平页面溢出、drawer 可关闭、表格卡片标签可读、确认内容不被截断；该人工视觉检查不执行真实设备发布。

## 只读检查

```bash
systemctl --user status tts-erp-publish.service
journalctl --user -u tts-erp-publish.service -n 100
```

不要执行真实 TikTok publish 或任意 ADB shell。发布健康检查只能调用 Artemis readiness/devices 只读接口。
