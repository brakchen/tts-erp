# tts-erp 部署与运维

## 部署拓扑

| 位置 | 生产职责 |
| --- | --- |
| 207（`207.57.126.199`，SSH 别名 `tts-erp-prod`） | Nginx 接收 `https://chen.daqiangplatform.com/tts`，保留 `/tts` 前缀转给本机 `127.0.0.1:9878`。系统级 `tts-erp.service` 从 `/opt/tts-erp` 运行。系统级 `tts-erp-sync.service` 从 `/opt/tts-erp-sync/releases/<sha>` 运行。Nginx、frps、API、Sync Worker 均配置开机自启。 |
| HP desk（`schan@192.168.1.2`，SSH 别名 `hp-desk`） | 运行 PostgreSQL、MinIO、frpc 和 PostgreSQL 备份。207 经 frp 的 `127.0.0.1:6009` 和 `127.0.0.1:6005` 访问存储；`/minio/` 仍经 `:6008` 转到 HP desk 网关。 |
| 开发机 | 目前无专用测试机。手动验证时启动绑定 `127.0.0.1` 的临时实例。自动化测试使用 [隔离测试入口](../guides/agent-testing.md)。 |

HP desk 上的旧 API、Sync Worker、Publish Worker 均已停止并关闭开机自启。
旧 Sync Worker 的单元、代码和配置保留供回退。旧插件地址 `http://207.57.126.199:6007`
的上游已停止，生产接入使用 HTTPS 公网入口。

迁移时核实的版本为 API `0b92d97d`、Sync Worker `723779dd`。前端迁移、调度注册等
主线代码的状态以 Git 为准，现场运行版本用下面的只读命令查询。

## 发布与回退

生产发布、服务重启和迁移由人工执行。执行前核对目标主机、当前运行版本、数据库 schema
及依赖兼容性。API 和 Worker 使用独立发布目录及 systemd 单元，共用 207 的
`/opt/tts-erp/.env` 和 Python 虚拟环境。

- 仅页面或 API 变化时，发布 API。仅同步实现变化时，发布 Sync Worker。
- 两者都受影响时，先确定一个已通过验收的完整 Git SHA，核对共享 schema 和依赖兼容性，
  再串行把该 SHA 传给两个入口。记录各服务切换前后的版本和健康检查结果。
- 只更新 Worker 时，确认共享 venv 已满足目标依赖。API 发布的 `--sync-deps`
  会改共享 venv，使用前应核对两份代码的兼容性。

```bash
git fetch origin master
target_sha="$(git rev-parse origin/master^{commit})"
# 核对 target_sha 是否为已验证的发布提交
bash scripts/prod_deploy.sh --dry-run "$target_sha"
bash scripts/prod_sync_deploy.sh --dry-run "$target_sha"

# 由运维执行受影响的服务发布
bash scripts/prod_deploy.sh "$target_sha"
bash scripts/prod_sync_deploy.sh "$target_sha"

# 只读查询两个运行版本
bash scripts/prod_deploy.sh --verify-only
bash scripts/prod_sync_deploy.sh --verify-only
```

- **API** 由 [prod_deploy.sh](../../scripts/prod_deploy.sh) 构建、打包并部署到
  `/opt/tts-erp`，重启 API 后核对本机和公网的健康、登录页及前端构建 ID。
  回退时传入已验证的旧 API SHA 重新发布。`tts_erp_v2.prev` 是应急代码副本。
- **Sync Worker** 由 [prod_sync_deploy.sh](../../scripts/prod_sync_deploy.sh) 从已提交的
  Git 对象打包到 `/opt/tts-erp-sync/releases/<sha>`。它预检 job 导入和 PostgreSQL
  连接，使用 systemd drop-in 切换工作目录，只重启 Worker，并检查本次启动的调度器日志。
  发布启动失败时尝试恢复此前配置。该脚本需要 207 已有系统级 `tts-erp-sync.service`。
- 两个发布入口均保留生产确认，人工确认后才允许用 `--yes` 运行非交互发布。
  数据库迁移按 [生产安全规则](../guides/agent-safety.md) 单独处理。

```bash
bash scripts/prod_deploy.sh "$previous_api_sha"
bash scripts/prod_sync_deploy.sh --rollback "$previous_worker_sha"
```

Worker 回退使用 207 上保留的完整 SHA 目录。若要退回 HP desk，先在 207
停用 Worker 并确认进程退出，再在 HP desk 执行
`systemctl --user start tts-erp-sync.service`。恢复到 207 前同样先停旧实例，保证任一时刻只有一个调度实例。

## 运行状态与日志

在 207 执行：

```bash
systemctl is-enabled tts-erp.service tts-erp-sync.service nginx.service frps.service
systemctl status tts-erp.service tts-erp-sync.service
cat /opt/tts-erp/.deployed-commit
worker_dir="$(systemctl show tts-erp-sync.service -p WorkingDirectory --value)"
cat "$worker_dir/.deployed-commit"
curl --fail --silent --show-error http://127.0.0.1:9878/healthz
curl --fail --silent --show-error https://chen.daqiangplatform.com/tts/healthz
journalctl -u tts-erp-sync.service -n 50 --no-pager
systemctl list-timers '*logrotate*'
```

| 日志 | 轮转 |
| --- | --- |
| ERP API `/opt/tts-erp/logs/{stdout,stderr}.log` | `tts-erp-logrotate.timer` 每六小时检查，单个文件达到 20 MiB 时用 `copytruncate` 轮转并保留 5 份，规则在 `/etc/tts-erp/logrotate.conf`。 |
| Nginx `/var/log/nginx/*.log` | Debian `logrotate.timer` 每日运行，Nginx 规则保留 14 份。 |
| Sync Worker、frps | 由 systemd journal 轮转，分别用 `journalctl -u tts-erp-sync.service` 和 `journalctl -u frps.service` 查询。 |

`/healthz` 返回 `service: "tts-erp-v2"` 表示 API 进程响应。Worker 发布后还应查看近期
任务结果；依赖故障分别核对 PostgreSQL、MinIO 和 frp。

### 生产 MinIO 预签名

207 的 `MINIO_ENDPOINT=127.0.0.1:6005` 用于应用经 frp 访问 MinIO。
浏览器经 `MINIO_PUBLIC_HOST` 的 `/minio` 路径访问时，HP desk 网关向 MinIO
转发 `Host: 127.0.0.1:9000`。207 的 `MINIO_PRESIGN_ENDPOINT=127.0.0.1:9000`
用于匹配该请求头。改动后在资产管理中核对上传、播放和拖动进度。

## 本地验证

使用 Python 3.13+。安装依赖和启动回环实例的步骤见
[测试指南](../guides/agent-testing.md#本地启动与手动验证)。`scripts/test_deploy.sh`
对应退役的 HP desk 测试发布流程。自动化测试、模板刷新和残留临时库处理按测试指南执行。

## 故障排查

| 症状 | 检查方式 |
| --- | --- |
| TikTok `106001 invalid sign` | 对照 [HMAC 签名规范](../reference/tiktok-hmac-signing.md) 检查 canonical 参数。 |
| TikTok `105005 Access denied` | 在 Partner Center 检查 app scope 和店铺授权。 |
| TikTok `36009004 PageSize is required` | 核对 Request Body 字段和 [分页实测](../reference/tiktok-open-api-pagination.md)。 |
| `?shop_id=` 没有过滤 | 从 `GET /v2/commerce/channel-accounts` 查内部 `shop_pk`。 |
| PostgreSQL 连接失败 | 先确认 HP desk 的 PostgreSQL 容器和 frpc，再核对 207 的 frps 与数据库隧道。 |
| 同步数据未更新 | 查看 207 的 Worker journal 和 `integration.sync_jobs` 的近期结果。 |

当前端点见 [外部 API](../api/external-api.md)，数据库结构见 [schema 索引](../schema/README.md)。
历史切流材料见 [文档归档](../archive/README.md)。
