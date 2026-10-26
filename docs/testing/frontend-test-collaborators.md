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


## N1：采购与视频的有限浏览器目标

N1 仍是同一个 `BrowserFence` 的测试专用扩展，不是第二白名单或权限 owner。默认不传 `remaining` 时保持原 P2 路径；只有原 `live_api_server(..., collaborators=True)` 启动的固定 profile、原 canary/probe、TEST 账户与 nonce/liveness 链可使用有限目标。

`RemainingTargets` 只携带原事实夹具给出的 `marker`、`shop_pks`、`product_pks`、`task_ids` 和 `dates`；这些集合不是许可。canonical fence 每次先读取原 nonce 绑定的固定 profile/receiver 元数据，再经原 checked Session 验证实际店铺、商品与任务归属。伪造 child 字段、独立的 PK 集合、错误前缀或未知目标不能产生授权。

- 仅准入采购和视频两页的有限页面/API。用户/角色、运行配置/秘密、拦截配置、同步任务、广告日明细和盈利变化六域仍拒绝；这不删减剩余八业务页的最终验收目标。
- 采购每个显式身份条件必须同时匹配同一 SQL 商品行及 owned `(product_pk, shop_pk, external_id)` tuple；不能分别命中不同集合。无内部 PK 的旧外部 ID 请求仅在全部实际候选均 owned 时才允许由原后端返回既有 409，不改写后端查询范围。
- `manual_pk_operand` 是唯一纯操作数规范化函数，不是 grant：既有 `spu_pk` / `shop_pk` 允许严格正 bigint 整数或等值 canonical ASCII 十进制字符串，范围为 `1..9223372036854775807`；字符串必须匹配 `[1-9][0-9]*`，长度在转换前限制为 1–19。拒绝 bool、float、类型子类、零、符号、前导零、空白、指数、小数、非 ASCII 数字及溢出。缺失与显式非法/null 不混淆，转发的原 wire body 不修改；schema 和 canonical 同行比较复用同一函数，不经浮点数。
- 打开采购页面前，测试调用方须在本轮原官方 clone/checked Session 内验证接口实际返回的 TikTok 店铺集合（服务端 ID 排序、limit 500）全部属于自有 marker/PK，再进入 DOM。只核最低 ID 不足以保证下拉列表无外部数据；发现外部可见行即停止，不响应过滤、全表删除、改原 canary 或为测试改变生产默认店铺。该前置不是新的网络许可 owner。

## N1 profile 元数据与受控传输

固定外层 `/__test__/remaining-console-v1/store` 及其 `pause` / `release` 只属于此测试 profile，默认 child 和生产应用没有该入口。它复用原 binding/nonce/proof；父环境标志、caller dataclass 状态或任意 URL 不能替代实际的固定元数据。receiver origin 来自实际本实例监听地址，仍与 ERP origin 分离。

视频 task/client/ticket 必须由真实 TEST API 与当前 SQL 关系学习，过期、撤销、来源错误或非本实例能力拒绝。原生二进制 PUT 只能由 canonical fence 完整校验请求头、来源、凭据、方法、MIME 和长度后 `route.continue_()`；不允许另一请求代理/缓冲重发、部分头替代或增加任意来源。暂停须在就绪后显式启用并在 finally 有界释放；取消意图不等于实际 EOF/短读。上传 URL/头值/载荷不日志，接收端只保留有界计数、size/hash 等元数据。

用户已接受当前 Chromium / Playwright 拦截自动预检由 driver 回答的行为，**接收端真实自动 OPTIONS 不再是本次单独门槛**；不得把它写成 receiver 已实际处理预检，也不另下载 Firefox、启用其他浏览器模式或安装系统依赖。真实 PUT/字节/hash/进度/实际取消、无 ERP 凭据边界、有效媒体与页面交互仍须分别取得证据。工具冷验通过不代表这些 native/媒体/视觉/运行条件完成。

支持层组合回归仍仅经官方 runner，在每次验证已确认的现存私有 `0070_monitor_health` 模板身份、schema 与源码 head 后 clone-only 执行。例如：

```bash
bash scripts/test_isolated.sh --template-db tts_erp_test_monitor_health_template fast \
  tests/scripts/test_frontend_p2_browser.py \
  tests/scripts/test_frontend_p2_live_support.py \
  tests/scripts/test_frontend_test_collaborators.py \
  tests/scripts/test_frontend_remaining_fence.py \
  tests/scripts/test_frontend_remaining_video_live_api.py \
  tests/scripts/test_frontend_remaining_video_projection_privacy.py
```

此组合必须保留父候选最新视频隐私 helper；旧 fence 分支的视频副本、未接纳的观察器或诊断脚本不随支持层整分支带入。不得把历史不同组合的通过数直接当作新组合结果。缺少或不匹配模板即停止；不 refresh/stamp、手工 DROP 或改用共享/生产库。
