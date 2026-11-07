# RunningHub 换装视频

入口 `/v2/pages/runninghub`，由现有 React/HeroUI 应用壳展示。API 前缀 `/v2/runninghub`；公网部署沿用应用的 root_path（如 `/tts`）。本功能不调用 TikTok 写接口，也不自动发布视频。

## 配置与上线

1. 人工按现行迁移流程应用 `0074_runninghub`，新增 `runninghub` schema 的 9 张表及 `page:runninghub` 权限（admin、operator）。迁移只新增对象；审计事实不提供破坏性 downgrade。开发验证仅由隔离 runner 升级测试模板。 在 HP desk 上运行 [`bash scripts/oneoff_migrate_0074_runninghub.sh`](../../scripts/oneoff_migrate_0074_runninghub.sh)，核对版本后输入 `tts_erp` 确认。
2. 在服务端私有环境中配置 [.env.example](../../.env.example) 的 RunningHub 项、已有 PostgreSQL 和 MinIO 配置。Key 不经过前端。`credentialRef` 默认 `env:RUNNINGHUB_API_KEY`，也支持 `env:RUNNINGHUB_<NAME>_API_KEY` 或 `integration:<external_account_id>`；后者通过既有 token_service 加载 provider=runninghub 的 access_token。不要修改或查询旧 oauth_tokens。
3. 安装 FFmpeg/FFprobe（须支持 libx264、AAC），确认 Worker 可执行二者；必要时填写绝对路径。准备私有临时目录和磁盘空间。原素材快照、raw、final 存入独立 MinIO bucket `tts-erp-runninghub`。
4. admin 检查账户、读取并校验官方完整工作流，或离线导入完整 API 图。启用的是不可变版本，旧批次保持原版本。当前只接受已映射的 `2111762608670990337`，plus=48GB 显存；不推断 GPU 型号，不开放任意帧率。
5. 人工安装/启动 [Worker unit](../../scripts/systemd/tts-erp-runninghub.service)，按实际部署覆盖 WorkingDirectory、EnvironmentFile、ExecStart；207 生产代码在 `/opt/tts-erp`，不能原样套用 HP desk 的用户目录。Worker 入口 `python -m tts_erp_v2.runninghub.worker`，`--once` 处理一个阶段后退出。API 不在 lifespan 启动队列，也不改变中间件顺序。

本次开发不执行生产迁移、生产服务重启或付费联调。真实模式只能由操作者创建 live 批次，再提交 `confirmPaid=true` 主动启动。

## 操作流程与接口

浏览器写操作需要现有 cookie + `X-Requested-With: tts-erp`；API Key 仍用既有 Bearer/X-API-Key。会话还需 `page:runninghub`。读取 readonly，计划/批次/模板/恢复/重生成 readwrite，配置更新/账户/工作流检查/提交核对 admin。没有匿名入口。

| 方法与后缀 | 行为 |
| --- | --- |
| `GET /config`、`PUT /config` | 无密钥配置；workflowId/实例/concurrency 1..5/凭据引用 |
| `POST /account` | 只检查账户，不生成任务 |
| `POST /dispatch/resume` | admin 在修复凭据/余额/工作流后以 `{confirmPaid:true}` 恢复当前凭据的新派发；保留全部在途与不确定任务的配额 |
| `POST /workflow/check` | `{graph:null}` 从官方读取；`{graph:{...}}` 离线检查并启用新版本，响应只含版本/hash/规格 |
| `GET /templates`、`POST /templates` | 保存人物 A/B/C 六段顺序；`humanId`、`name`、六个不同的 `assetIds`，视频须已挂载该数字人 |
| `POST /plan` | 校验 `images`、`templateIds`、`single`，返回数量、组数和全部归档路径；不复制素材、不生成 |
| `POST /batches` | 加 `requestId`、`mode`（默认 dry-run）、可选 `instructions`（173/174/185）；保存完整计划/素材快照/工作流版本/种子，不自动启动 |
| `GET /batches[/{id}]` | 状态计数、每组完成数、十进制费用及未知项 |
| `POST /batches/{id}/start`、`/pause`、`/resume` | JSON `{confirmPaid:false}`；live 的启动/恢复要求 true；暂停不取消已受理云端任务 |
| `GET /batches/{id}/jobs`、`GET /jobs/{id}` | 单任务与所有 Attempt；状态、hash、脱敏回执和费用 |
| `POST /jobs/{id}/resolve` | admin 提供 `taskId`，或 `confirmedNotCreated:true`，二选一；必须有核对说明 `note` |
| `POST /jobs/{id}/recover` | 已知 ID 的查询/下载/后处理恢复，不创建新生成任务 |
| `POST /jobs/{id}/retry` | 仅明确失败/素材准备失败可重新安排，生成新 Attempt，保留旧 ID/费用；没有“重试所有” |
| `GET /jobs/{id}/artifacts` | raw/final 规格、hash 与短期预签名预览/下载 URL；签名 URL 不写普通日志 |
| `GET /batches/{id}/export` | 仅全部完成才生成 ZIP；批次按款式/模板/人物/原图片名归档，无占位视频 |

列表使用 limit/offset：模板最大500（默认100）、批次最大100（默认30）、任务最大500（默认100）。图片输入示例：

```json
{
  "requestId": "operator-generated-stable-request-id",
  "mode": "dry-run",
  "single": true,
  "images": [{"source":"media","assetId":"123","style":"STYLE-1","skuId":"SKU-1"}],
  "templateIds": ["template-version-id"]
}
```

`source=media` 选择全局素材库图片；`source=spu` 使用已有 SPU 图片 ID（服务端验证店铺授权），无需再次上传。`skuLabel` 可选，默认保留原图片名去扩展名；真实 skuId 与文件名独立保存。页面当前从素材库选择图片，SPU 图片可通过同一 API 输入。数字人和模板数量不写死。普通批次每个组合有 1..6 六个明确片段；`single=true` 是主动单任务联调入口，只取一个模板的第1段，明确不视为六段批次。九图×三人×三模板的离线计划=486任务/81组。

模板和批次创建会按对象 ETag 条件复制素材到任务 bucket，后续源库整理不破坏运行批次。每个 Attempt 在 Worker 准备阶段读取固定素材，保存 SHA256；同凭据 hash 的上传缓存保守使用20小时，明确失败的新 Attempt 会重传素材。路径清理后碰撞即报错，不覆盖或混合不同图片。

## 恢复、调度与完成语义

数据库 quota 行锁和唯一约束控制同凭据的提交预留/排队/运行/不确定窗口总计最多5个；较低配置优先，本地下载/编码另以数据库锁限2个。每轮优先恢复已有任务，多个 Worker 可并行工作。新单独 Worker 进程不是第二套应用；复用 PostgreSQL 的现有持久化模式，不引入 Redis/Celery。

create 前提交 SUBMITTING 意图，HTTP 发送一次，不透明重试。收到 taskId 立即单独提交，之后再处理 promptTips。超时/无法解析/Worker 在提交中崩溃进入 SUBMISSION_UNKNOWN，配额保留。只能管理员查平台任务列表后关联 ID 或确认未创建；官方五接口不提供按业务 ID 找回能力。提交中的15分钟 lease 过期不会重提。已受理任务轮询间隔10秒；查询错误按20..300秒退避，12次连续错误或整体1小时等待转 TRACKING_INTERRUPTED，仍保留 taskId/配额并可主动恢复。未知云状态保存并暂停新派发，不当成功。

只有官方 SUCCESS/FAILED 才释放受理任务的云端配额。SUCCESS 后进入 DOWNLOADING/FINALIZING。查询或下载使用原 taskId；过期 URL 重新查原 ID，取不回时明确失败，不自动再生成。优先节点150；V2 无节点信息时逐个下载候选并用 FFprobe 选择唯一576×1024且足够长的主视频，横向188不当成片，多个符合候选报错。

保留 raw，FFmpeg 参数数组生成5秒/24fps/576×1024/120帧视频，验证时长容差0.05秒；音轨可选。有任何下载/编码/发布失败，状态为对应本地阶段失败，recover 仅恢复该阶段。只有已校验 final 成功持久化后 SUCCEEDED。默认 dry-run 会验证实际素材并使用灰色视频走同一个 raw/final/存储链路，不联网 RunningHub，也不代表生成质量验证。

费用按唯一云 taskId（含历史 Attempt）用 Decimal 汇总顶层 usage；轮询和下载链接刷新共用回执记录出口，合并实际出现的字段，避免后一次部分响应丢掉已观测值。RH币与钱包币种分开，显式null保持未知，不与taskUsageList重复相加；完整可选列表保留在脱敏诊断中，不猜测未定义结构的金额。并发限制只覆盖本功能；accountStatus 的其他活动是观察值，不是远端配额预留。没有使用取消、Webhook、retainSeconds 或自动质量迭代。

官方错误码804/813表示已运行/已排队：有taskId则继续跟踪，没有ID保留额度待核对，禁止当作失败重生成。416/812余额、802/811凭据、380/803/810工作流等配置错误会暂停该凭据所有批次的新派发，仍跟踪已受理任务；修复后由admin主动恢复。普通云终态失败只暂停所属批次。较低并发配置优先；要提高曾降低的账户配额，需要admin明确保存新配置（仍不能超过5）。

## 上游与依赖依据

2026-11-07 已读取官方 [账户](https://www.runninghub.cn/runninghub-api-doc-cn/api-425748943.md)、[工作流](https://www.runninghub.cn/runninghub-api-doc-cn/api-425749014.md)、[上传](https://www.runninghub.cn/runninghub-api-doc-cn/api-425749007.md)、[高级任务](https://www.runninghub.cn/runninghub-api-doc-cn/api-425749013.md)、[V2查询](https://www.runninghub.cn/runninghub-api-doc-cn/api-425767306.md)。区别：账户 body 使用 `apikey`，工作流/创建使用 `apiKey`，V2查询直接读取顶层 status/results。multipart读取fileName，完整workflow发送JSON字符串。上传有效期一天是上游说明；20小时缓存是本系统保守策略。

检索了仓库、官方 GitHub 示例和 PyPI；没有适合替代本项目持久化语义的官方 SDK。沿用已安装的 httpx>=0.27（BSD-3-Clause，[传输配置](https://www.python-httpx.org/advanced/transports/)）、SQLAlchemy>=2（MIT）、MinIO>=7.2（Apache-2.0）、HeroUI3.2.6（MIT）；不新增运行时 Python/npm 依赖。FFmpeg/FFprobe 由部署环境提供，不复制用户参考脚本或单机文件队列。
