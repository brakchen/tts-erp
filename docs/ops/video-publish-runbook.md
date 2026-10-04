# TikTok 视频发布运维手册

## 边界

浏览器只访问 ttsERP；视频通过短期预签名 PUT 直传私有 MinIO。Worker 是唯一可访问 ADB/Artemis 的进程，数据库保留 bucket/key/ETag 和每个 Artemis session。禁止用真实任务做健康检查。

## 部署顺序

1. 使用测试形数据库验证 video-publish migrations `0053`–`0056`，再由运维执行生产迁移。
2. 创建私有 `tiktok-video` bucket，并限制 `video-publish/*` 的 Get/Put/Delete/Head 权限；配置来源站点 PUT/HEAD CORS。
3. 创建 `TTS_ERP_PUBLISH_SPOOL_DIR`（0700），设置 `ARTEMIS_BASE_URL`、`ARTEMIS_DEVICE_SERIAL`、`ARTEMIS_APP_PACKAGE`、`TIKTOK_PUBLISH_MINIO_BUCKET=tiktok-video` 和 MinIO 凭据。`MINIO_BUCKET` 必须与专用 bucket 相同；未配置设备序列号或 bucket 不匹配时 API 拒绝创建任务。
4. 安装 `scripts/systemd/tts-erp-publish.service`。生产环境如需执行对象删除，须由运维在服务环境显式设置 `ALLOW_PROD_DESTRUCTIVE=1`；缺少该授权时对象保留并标记 cleanup failed，不会静默删除。启动后确认 `publishing.worker_heartbeats` 在 15 秒内为 ready。
5. 仅用模拟器/fake adapter 做 staging dry-run；真机先只做 MediaStore dry-run。
6. 给 operator/admin 授权 `page:video-publish`。首次真实发布必须由用户显式确认。

## 故障处理

- Artemis 请求超时：Worker 查询并复用原 `artemis_session_id`，不要手工新建 session。
- 结果不确定：必须先 verify；`needs_review` 禁止普通 retry。
- 设备文件、spool、MinIO 是独立清理状态。成功任务的清理失败只执行“重试清理”，不能改写业务成功。
- 设备清理失败时，Worker 会阻止后续任务领取/进入 staging，并按退避自动领取到期的成功任务重试；对象和 spool 清理失败也会独立按退避自动重试，不改变业务成功。任务详情的“重试清理”仅用于在无活动清理租约时立即提前触发重试；取消任务的对象清理失败同样保留 cancelled 状态并可恢复。
- 页面智能刷新以 API 返回的未过滤运行/排队摘要决定节奏，不会因切换到成功/失败筛选而放慢待处理任务；awaiting-upload 草稿不计入排队，选择“关闭”时运行轨道显示“自动刷新已暂停”，重新启用后恢复轮询。
- 上传中的“取消上传”只 abort 当前 XHR，不确认任务；草稿保留原 clientRequestId，可通过“继续上传”恢复。
- Worker 重启会从 running task 继续读取原 session；不要删除数据库行或对象。
- 回滚时先移除页面权限、停止领取新任务；保留 running/needs_review 的对象和审计历史。只有三张表为空并经人工确认才允许 downgrade。

## 只读检查

```bash
systemctl --user status tts-erp-publish.service
journalctl --user -u tts-erp-publish.service -n 100
```

不要执行真实 TikTok publish 或任意 ADB shell。发布健康检查只能调用 Artemis readiness/devices 只读接口。
