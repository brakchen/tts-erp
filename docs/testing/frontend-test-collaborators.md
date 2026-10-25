# 受限前端协作者测试接点

本接点只属于 `tests/`。它用于在**本轮官方隔离 clone**内验证真实 API/鉴权/SQL，并将同步任务执行、视频存储和设备访问隔离为受控替身。不是生产接口、运行开关、真实发布成功证明，也不是新的数据库/provenance owner。

## 启用方式与唯一入口

```python
from tests.support.frontend_p2_binding import current_binding
from tests.support.frontend_p2_live import live_api_server

value, binding = current_binding()
with live_api_server(value, collaborators=True) as child:
    # 原 canary/readback、TEST account 与 browser egress fence 仍必须使用。
    ...
```

- 默认 `collaborators=False` 的命令、factory、原验证顺序和应用挂载保持不变。
- 参数只接受真正的 `bool`。`True` 仅选择固定 `remaining-console-v1`；不接受任意 factory、模块、回调、URL 或路径。
- 父环境中同名标志由 `child_environment` 清除，只有本次显式启动才能写入固定 profile。子进程拒绝空值/未知 profile。
- 唯一 uvicorn factory 仍是 `tests.support.frontend_p2_live:create_app`。原 run proof、父进程 PID/start、真实 Engine/Session、私有 attestation、HTTP nonce、持有的 Popen liveness、enforce/no-dotenv 校验不删除、不替换。安装后和替身真正激活后再次复用原 `validate_application`。
- `runner`、`conftest`、绑定/proof 格式、鉴权、会话、生产 middleware、业务 API 和数据库 schema 均不修改。任何生产形状、旧 proof 或错误绑定依然拒绝。

## 三类替身

| 协作者 | 行为 | 不代表什么 |
| --- | --- | --- |
| `admin.run_scheduled_job_once` | 只在子进程内记录 `(job_name, shop_id)`；原 API 的权限、CSRF、任务/店铺校验不变 | 不运行同步，不伪造成功/失败的业务事实，不证明真实作业完成 |
| 视频 `get_store` 依赖 | 独立 loopback HTTP receiver、受控 PUT/OPTIONS、内存对象元数据/版本，支持真实 API 的上传确认 | 不访问 MinIO，不运行发布 Worker，不保存上传载荷，不证明真实对象存储/生产上传可用 |
| `ArtemisClient._request` | 只返回固定 `TEST_device` 的设备读取；其他设备操作明确拒绝 | 不连接真实 Artemis/设备，不执行发布/验证/清理命令 |

仅对上述协作者及其 TEST 配置作生命周期内替换。现有依赖 overrides、模块函数、环境值在结束时恢复；不替换 `get_session`、DB getters、角色/认证、业务状态机或任务目录。

worker heartbeat、角色、任务等事实仍由调用方经原 checked Session 明确创建 TEST 夹具。本接点不自动插入“Worker 就绪”或其他业务事实。

## 生命周期与挂载

FastAPI/Starlette **不会自动运行被 Mount 的子应用 lifespan**。固定 profile 返回仅管理替身的 async context manager，由测试外层 Starlette 持有；不新增或启动原先没有执行的生产应用 startup/shutdown 事件。

来源：[FastAPI Lifespan / Sub Applications](https://fastapi.tiangolo.com/advanced/events/#sub-applications)。采用现有 Starlette/FastAPI、pytest `MonkeyPatch` 公有接口，未新增依赖。

## loopback 存储边界

- 只监听 `127.0.0.1` 随机端口，生命周期结束关闭；真实 API origin 与此上传 origin 分离。
- 只有本实例铸造的能力路径可 PUT；CORS 仅允许与 ASGI 实际监听地址一致、经该实例验证并登记的精确 loopback origin。伪造不同端口的 Host 不产生授权。
- 不允许 Cookie、Authorization、X-API-Key 或 ERP CSRF 头；不返回 `Access-Control-Allow-Credentials`。OPTIONS 仅允许 PUT/Content-Type。
- 仅接受声明长度且不超过 32 MiB 的 `video/mp4`。只有完整收到的内容才产生内存 size/opaque ETag/SHA-256 元数据；不保存原始字节。
- 不日志上传 URL、header 值、cookie 或 payload。无法处理的 Worker 下载明确拒绝；不会回退真实存储。remove 仅影响本实例内存对象并保留版本检查。
- 该接点不提供前端请求白名单或浏览器来源授权；调用方仍须维护原 egress fence，并先验证 owned child/canary。

## 验证范围

最窄命令（必须通过官方 wrapper）：

```bash
bash scripts/test_isolated.sh --template-db tts_erp_test_monitor_health_template fast \
  tests/scripts/test_frontend_test_collaborators.py \
  tests/scripts/test_frontend_p2_live_support.py
```

此私有模板仅在 owner 已确认存在且源码 migration head 为 0070 时 clone-only 使用；不得 refresh/stamp/drop，也不得套用 0069 源码。完整基线按既有环境入口选择同一模板，不修改 runner 或已提交 baseline：

```bash
TTS_ERP_TEST_TEMPLATE_DB=tts_erp_test_monitor_health_template bash scripts/test_baseline.sh
```

测试分层：

1. 接点/环境拒绝、默认序列、安装顺序、错误绑定及失败不签发证明。
2. 外层挂载实际生命周期、替身恢复、原应用 startup 不被额外调用。
3. loopback 实际 PUT/预检、精确 origin、拒绝 ERP 凭证、对象元数据。
4. 独占 clone 冷启真实子进程，root 和 `/tts` 的真实 TEST 登录/CSRF、设备替身、创建任务、受控上传、真实 SQL 上传确认/独立读取、同步入口 no-op、真实登出，逐响应验证原 nonce/attestation。

第 4 层使用独立 HTTP client 上传，**不是浏览器 cookie/CORS/媒体进度验收**。剩余页面的 native、截图、完整业务操作、精确 master 和运行发布仍需各自的证据；本接点绿色不能代替它们。

## N1 未发布的两页护栏切片（不是八页验收）

`BrowserFence` 的新 opt-in **目前只支持 manual-costs / video-publish**；原 P2 默认四页和默认启动流程不变。
其余六页 `ad-daily`、`spu-profit-deterioration`、`users`、`runtime-configs`、`intercept-configs`、`sync-jobs`
及其新增业务接口明确拒绝，留待 N2。生产页面并未删除；整体八页目标仍未完成。

```python
from tests.support.frontend_remaining_targets import RemainingTargets
# 只接受下列字段；PK 集合为 frozenset[int]，task_ids / dates 为 frozenset[str]。
targets = RemainingTargets(marker="TEST_example", shop_pks=frozenset(),
                           product_pks=frozenset(), task_ids=frozenset(), dates=frozenset())
# live_browser(..., remaining=targets)；默认 remaining=None 不启用此切片。
```

- 每次新授权前，先由原 probe/child/nonce 验证固定 TEST 外层元数据接点；调用者可替换的 `collaborator_profile` 字段不是授权证据。
- 现存店铺 `account_name`、商品 `title` 必须与 marker **精确相等**，PK 必须实际存在；商品与店铺、外部 SPU ID 是同一条实际 SQL 记录的关系，不是三个独立集合。
- 人工成本的每个显式身份条件必须同时对应同一商品。只有外部 ID 且存在歧义时，仅当全部实际候选均为 owned 才允许由后端返回原有 409；绝不改写后端解析范围。
- 新商品/成本列表限定具体 owned shop；若该店铺可见商品中包含未登记目标，拒绝，而非过滤响应。视频 tasks/current/list 属于有限全局读取；若实际表中存在未登记任务，拒绝。config/devices 只读取固定测试 profile 配置与设备替身。不依赖模板业务数据为空。
- 视频任务必须由当前真实 TEST 登录用户创建；任务/客户端 ID/请求字段须与已 attested 响应和真实 SQL 一致。重发必须响应原路径的同一任务；新票据使原票据失效，取消/确认上传也移除该任务票据。票据绑定 task/client/size，不接受调用者 URL。
- origin 只从原 nonce 包装的固定接点学习。观察字段严格封闭为计数、布尔、SHA-256，不扩大为任意标量数据。`gate.observe_store()` 返回安全观察；`gate.store_control(page, 'pause'|'release')` 只走原固定接点。
- 固定 TEST 外层 GET `/__test__/remaining-console-v1/store`（及 `/tts` 挂载）；POST 仅 `/pause` 和 `/release`，必须原 nonce。默认 factory 不安装；生产 ERP auth/router/middleware 未修改。
- native PUT 保留 `all_headers()` 凭证拒绝、精确 origin/MIME/声明长度及 32 MiB 上限，并只 `continue_()`。这只是实现约束，**尚未证明 native 实际通过**。receiver 的 EOF 短读才是取消事实，释放控制不是取消证据。

本轮只授权冷启动 profile/ownership 回归；不得重跑先前四组 UI native 失败测试。原 native 文件是未完成 WIP，非通过证据、非跳过的验收项。
后续 Option B 需要 Parent 单独授权：同一浏览器 origin、同一 canonical fence、实际 attested ERP task/ticket 的 test-only XHR 消费者，
仅证明 CORS/OPTIONS/PUT/progress/EOF-abort，不新增页面/路由/传输/鉴权旁路，也不冒充实际视频 UI 可用性；真实 MP4 与 UI 仍待独立验收。

N1 允许的新增请求形状（不是页面全部交互已完成）：

| 领域 | 有限形状 |
| --- | --- |
| manual-costs | GET 商品/缺成本/成本列表：显式 owned shop、有限分页及字段；GET 精确 owned product；POST 人工成本：实际同一商品身份元组 |
| video-publish | GET config/devices；GET 无查询 tasks/current/tasks（全部可见任务须 owned）；GET 精确 owned task，可选 `includeDiagnostics=true` |
| video-publish 写 | POST 创建固定 marker 文件名/文案的 MP4 上传任务（≤32 MiB）；POST 精确 task 的 upload-url `{}`、confirm-upload/cancel `{rowVersion}` |
| 控制观察 | 固定 TEST 外层 metadata/pause/release，原 nonce/current binding 验证 |

视频 status/cursor 分页、retry/replace-upload/verify/settings/cleanup 等其余写动作目前拒绝。
这意味着即使两页冷护栏通过，也不能称两页完整 UI 功能或原生上传验收通过。默认 P2 已有 API 形状不因此扩大。
