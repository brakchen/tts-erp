# 广告计划状态同步技术方案

> 状态：评审稿，尚未实施。2026-10-09；2026-10-10 按用户指示更新插件位置与源码基线。
> 契约所有者：TTS-ERP；消费者：本仓库独立子模块 `plugins/ads-data-sync`。
> 本方案依据本轮用户已确认要求：插件与 ERP 同时修改；每页只上传 Seller／Advertiser 与整页响应；不增加客户端采集时间、批次或页码；不额外归档原始响应；仅新增接口的 request body 原样写本机日志、不脱敏、保留两天；不捕获认证请求头。

## 1. 目标与非目标

每次既有 `post_campaign_list` 分页请求成功后，插件将该页完整 JSON 响应上传给 ERP。ERP 按 Seller、Advertiser、`campaign_id` 保存三个当前状态字段，供后续业务读取。

- 这里的 ID 是广告计划 `campaign_id`，不是素材 `ad_id`。
- 本次仅补充状态数据，不修改 TikTok 广告开关、不新增 TikTok 出站调用。
- 不修改 `ad_daily`／`ad_today` 的历史金额、日期、覆盖记录或同步检查点。
- 不用状态改变 SPU 关注范围，不实现状态页面、额外查询接口或开关历史还原。
- 不扩大原有发现的日期范围、触发频率或页面拦截范围，不承诺全店计划覆盖或实时状态。
- 新链路不写 `ad_raw_log`／`plugin_logs`，既有统计、操作日志及其他接口的日志策略不变。

## 2. 现状、复用与方案选择

### 2.1 源码基线

- ERP：`ff5122872567aa2b456434b9607dc49f1e2104a1`；建立方案工作树前仅增加 lane 登记提交 `13488726f31b03a0c72f746f8183f5ee439179ab`。
- 当前插件位置：`/home/schan/tts-erp/plugins/ads-data-sync`，直接属于 TTS-ERP 的独立 Git 子模块，remote 为 `git@github.com:brakchen/ads-data-sync.git`；本次路径复核 HEAD 为 `5fa6e7add30a4307890abd61750dea6784f9f23f`（0.1.244）。不再从旧 `chrome-plugins` 父仓库或旧目录取源码。初次调查的 0.1.232／0.1.233 仅为历史证据；新位置的 background 和计划发现模块已变化，实施前以当前子模块及届时最新 remote master 重读。实现使用插件独立任务工作树，不直接改 ERP 固定的子模块检出，也不把插件文件作为 ERP 提交内容。
- `src/core/all-campaign-discovery.ts::discoverAllCampaigns` 已顺序遍历全部查询分页；`CampaignDiscoveryExchangeObserver` 已拿到每页完整响应。
- `plugins/ads-data-sync/entrypoints/background.ts::discoverBoundCampaignsOnce` 仍将发现结果投影为计划 ID 与创建日；查询结束日为店铺当地昨天，范围受现有历史同步设置限制。新基线增加非 heartbeat 发现前的 coverage 预检及绑定／范围变化后的有界重试；状态接线必须保留这些行为，不能套用旧 background 覆盖新逻辑。
- `src/core/daily-progress.ts::dailyEndpoints` 只选择商品分析和操作日志，不包含计划列表。
- `src/core/runtime-observability.ts::runtimeLogContext` 会压缩诊断响应。新状态上传必须使用直接取得的响应，不能从 runtime 日志、下载诊断或完成检查点恢复正文。插件 README 的“完整正文”描述与该实现不一致，不把描述当成传输保证，也不顺带改变全局 runtime 日志策略。
- `tts_erp_v2/api/v2/analytics.py` 已有 `ScopeIn`、scope 授权、2 MiB 原始请求尺寸闸及同步数据库 handler 模式；`access/_policy.py` 将 `/v2/analytics/sync*` 归为 readwrite。
- `tts_erp_v2/db/models/plugin.py` 仅有按日广告统计、原始 dump、运行日志和操作历史，没有广告计划当前状态实体。
- 已有 `scripts/logrotate/tts-erp.conf` 与 `scripts/systemd/tts-erp-logrotate.timer`：用户级 timer 每六小时检查 stdout／stderr 等应用日志。现有 stanza 不提供新增正文日志的两天保留。

### 2.2 选择

| 方案 | 结论 |
| --- | --- |
| 从通用拦截记录解析状态 | 保留为既有诊断来源，不作为本次业务写入链路；依赖实际浏览页覆盖，不能代替插件已取得的分页响应 |
| 在商品 daily dump 中附带状态 | 不采用；其单计划／单日期契约与多计划列表不匹配，会重复传输或让当前状态混进历史事实 |
| 独立状态接口，复用发现分页回调 | 采用；一页一次上传，只更新当前状态，最小协议不增加客户端元数据 |

采用现有 SQLAlchemy／PostgreSQL upsert 和 Python 标准日志组件，不新增第三方依赖、不自建轮转框架：

- [SQLAlchemy 2.0 PostgreSQL upsert](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#insert-on-conflict-upsert)：与项目 SQLAlchemy 2 兼容，已有依赖，MIT。
- [Python 3.14 WatchedFileHandler](https://docs.python.org/3.14/library/logging.handlers.html#watchedfilehandler)：Linux 上配合外部 logrotate 的文件重开机制，标准库，PSF 许可。
- [logrotate 手册](https://github.com/logrotate/logrotate/blob/main/logrotate.8.in)：复用已安装工具及既有用户 timer，不增加调度进程。

## 3. 接口契约

新增 `POST /v2/analytics/sync/campaign-status`，复用扩展现有 Bearer 凭证与 scope 授权。必须具有 readwrite 角色；Seller／Advertiser 不在授权范围时返回 403，不更新状态，也不记录该未授权正文。

请求只有两个顶层字段：

```json
{
  "scope": {
    "sellerId": "TEST_seller",
    "advertiserId": "TEST_advertiser"
  },
  "response": {
    "code": 0,
    "data": {
      "table": [
        {
          "campaign_id": "TEST_campaign",
          "campaign_primary_status": "delivery_ok",
          "campaign_opt_status": "0",
          "campaign_status": "campaign_delivery_ok"
        }
      ]
    }
  }
}
```

示例为合成测试数据；真实响应的其他字段、`table_v2` 与分页信息原样传输、原样写入正文日志，但不额外提取保存。接口只从 `response.data.table` 更新状态，不从 `table_v2` 重复写入。

- 顶层不需要 `protocolVersion`、`kind`、`day`、`campaignId`、采集时间、批次或独立页码。
- `scope` 的两个标识为非空字符串，沿用现有最大长度限制；`response` 必须为对象，`data.table` 必须为数组。
- 若响应携带 `code`，非成功业务码必须拒绝，不能凭 HTTP 200 写状态。
- 每行必须包含非空 `campaign_id` 和三个状态键；状态值接受字符串或显式 null。空值／未知字符串不解释为关闭，不把字符串 `"0"` 转成布尔值。
- 与现有计划发现兼容，`campaign_id` 可接受 JavaScript 安全范围内的正整数并规范为十进制字符串；拒绝布尔值、浮点数、不安全数字及空标识。大 ID 必须使用字符串，不能接受浏览器已经舍入的数字。
- 一页内重复 `campaign_id` 必须拒绝，避免暗中选择冲突状态。任一行结构非法则整页拒绝，不部分更新后返回成功。合法空 `table` 返回成功但不删除、不关闭任何计划。
- 请求超过 2 MiB 返回 413；无效 JSON 返回 400；结构或上游业务失败返回 422；持久化或正文日志写入失败返回 500；认证／授权失败分别为 401／403。
- 成功使用现有 envelope：`{"code":0,"requestId":"…","data":{}}`。只在完整解析与事务持久化完成后返回 2xx，不增加 `rowsWritten` 或 `data.status` 等并行成功信号。

## 4. 当前状态存储

新增 `plugin.ad_campaign_status`，不把三字段塞入每天的统计记录，也不把日志当业务表。

| 字段 | 类型与规则 |
| --- | --- |
| `id` | bigint identity，内部主键 |
| `seller_id` | text，非空 |
| `advertiser_id` | text，非空 |
| `campaign_id` | text，非空，计划 ID |
| `campaign_primary_status` | text，可空，原始值 |
| `campaign_opt_status` | text，可空，原始值 |
| `campaign_status` | text，可空，原始值 |
| `created_at` | timestamptz，首次入库时间 |
| `updated_at` | timestamptz，最近一次成功状态接收时间 |

唯一键为 `(seller_id, advertiser_id, campaign_id)`。首次看到计划则插入；后续只更新该计划三个状态字段及 `updated_at`。不修改其他计划，也不删除未在页面出现的记录。

- 使用同一事务对整页批量 upsert。任何数据库异常回滚整页；不关闭已有非本次计划。
- 不新增布尔 `enabled` 或自建投放分类；三字段原值是本次交付边界。后续消费者在后台根据核验后的规则解释开关与投放原因。
- 时间统一由后端生成 aware UTC；更新时间不是 TikTok 采集时间。
- 无客户端采集时间，按后端成功写入顺序覆盖；同页重复提交不产生重复计划行，但会刷新接收时间。不提供 exactly-once、不声称防止旧请求迟到覆盖。
- 不保存完整响应 JSONB、原始请求 JSONB、页码、批次、分页快照或计划状态历史。
- 新增前向建表迁移，迁移编号和 down_revision 以实施时真实 Alembic head 确认，不预占其他 lane 编号。只通过隔离测试验证；生产迁移由人操作。

## 5. 插件改动

1. 新增小型 `src/core/campaign-status-sync.ts`，负责最小请求构造、同源 ERP 目标 URL、既有 Bearer 凭证及有界 HTTP 上传。
2. 将计划发现的现有 observer 组合为“记录现有诊断＋上传状态”。使用发现开始时冻结的 Seller、Advertiser、ERP 地址和凭证，不从迟到回调读取新店铺身份。
3. 复用 `all-campaign-discovery.ts` 已有分页 observer，仅对 HTTP 2xx、无传输／JSON 读取错误的响应发起状态提交；后端负责业务码、列表结构和状态行验证，失败响应不得写入状态。发现自身的分页一致性验证、失败诊断与结果投影保持不变。必要接线测试落在既有发现模块，不为上传改造整个采集框架。
4. 每个有效分页最多进行一次正常提交；上传 response 对象完整且不走 runtime 日志压缩、商品响应 compact 或 `extractRowsForV4Dump`。
5. 每次上传前检查发现 lease 与冻结作用域仍有效。作用域变化后停止旧页提交，不把旧店铺数据写到新身份；已成功提交的旧身份记录不删除。
6. 复用现有上传失败处理实践：每次 HTTP 超时 20 秒；网络错误、429、5xx 在内存中最多尝试三次，401／403／404／422 等非重试错误不重复提交。使用同一已取得响应重试，不重新请求 TikTok。
7. 状态上传失败写错误诊断，但不抛出导致广告计划发现／商品统计同步失败；接着处理后续分页。发现不能因为状态上传失败而丢掉计划 ID。
8. 首版不引入新的持久化待上传队列、后台发现定时器或 UI。扩展 Worker 重启会丢失未确认的内存重试；后续既有发现再次观测补充，不能声称状态可靠持久重试已交付。
9. 插件按发布规则同步版本文件、CHANGELOG 与 README；技术契约链接到 ERP 本方案，插件只记录本地接线影响，不复制一份协议。

## 6. 本机正文日志与两天保留

本轮用户明确授权：仅新增接口的 request body 不脱敏、不裁剪。适用范围严格限制在该新接口经身份与 scope 授权的、尺寸闸内请求，不改变其他日志策略。

- 先解析并验证 scope 授权，再记录正文、校验响应及写入状态。scope 已通过授权的响应结构／状态行校验失败和数据库失败，同样保留原请求正文供排错；无效 JSON、缺少合法 scope、角色／scope 拒绝只记录既有错误摘要，不记录无法确认授权作用域的正文。采用 JSONL 日志记录原始 body 文本，JSON 转义换行以防日志伪造；转义不是脱敏，正文可完整还原。
- 写入独立 `logs/campaign-status/requests.log`，目录仅服务用户可访问、文件模式 0600。允许测试通过临时目录注入日志路径，不创建或读取真实业务日志。
- logger 使用标准库 `WatchedFileHandler`，禁止传播到 stdout／stderr，避免复制到其既有更长保留期。日志不进数据库、MinIO、Git 或插件运行日志上传表。
- 不记录 HTTP headers，不主动追加 Authorization、Cookie、API key 或插件设置。原样业务正文可能含敏感数据，这是已授权的局部策略例外；不要把真实日志放入测试 fixture、提交或聊天输出。
- 在既有 logrotate 配置中新增仅针对该目录的 stanza：`daily`、`rotate 1`、`maxage 2`、`ifempty`、`missingok`、`create 0600`；rename 轮转而非 copytruncate，配合 WatchedFileHandler 重开。
- 仅保留当前文件和上一日轮转文件。既有用户 timer 每六小时检查，`ifempty` 保证无新请求时也继续轮转清理。两天是按日轮转的正常运行保留窗口，不是精确到秒的 48 小时删除 SLA；timer 延迟或停机会推迟实际清理。不得宣称单纯配置 `maxage` 就已验证自动清理。
- 不修改 stdout／stderr／watchdog 的原有 stanza、timer 频率与保留策略。发布时由人验证现有 timer 启用及新目录权限。
- 正文日志写入失败不静默吞掉：返回 500，不继续更新状态。数据库失败允许保留该请求日志，但不返回成功。未经授权请求不将正文复制进专用日志。

## 7. 实施切片、文件与验收

### 7.1 ERP

预计修改 `api/v2/analytics.py`（复用现有 router、scope、原始 body 依赖，仅增加薄 handler）、新增 `plugin/ads/campaign_status.py`（验证／状态写入）和 `plugin/ads/campaign_status_log.py`（专用日志）；修改 `db/models/plugin.py` 与必要模型导出；新增真实 head 后的迁移、接口／仓储／日志测试；修改 `scripts/logrotate/tts-erp.conf`；更新端点契约链接。

当前 `docs/handoff/ACTIVE.md` 的 video-publish-design lane 保留 `db/models/` 与 `docs/api/`。已发热点协调消息，获得文件共享边界或交换最小补丁前，不直接编辑其文件。不能用影子 model、绕过中央 metadata 注册或修改历史统计表来回避协调。接口契约及持久化边界在本方案评审后进入既有契约／架构记录，其他仓库只引用 ERP 所有者；草稿不作为已接受 ADR 或上线依据。

### 7.2 插件

插件源码所有者为 `plugins/ads-data-sync` 独立仓库，以下路径均相对此子模块根目录：预计修改 `entrypoints/background.ts`、`src/core/all-campaign-discovery.ts`；新增上传模块及测试，补充分页 observer／冻结作用域接线测试；按发布规范更新 `package.json`、`package-lock.json`、`wxt.config.ts`、README、CHANGELOG。插件在自己的任务分支／工作树提交，ERP lane 不在 `plugins/` 中执行暂存、不纳入插件源码或其他子模块。只有有意推进已验证插件提交时才单独处理 ERP 的子模块 pin；Chrome 安装与发布另行交接。

### 7.3 验收与测试

跨仓库协议和状态持久化风险采用先写失败测试再实现的路线；不调用真实 TikTok，不执行生产写入。

- 正常一页、多计划、多页上传；插件只发送 `scope`／`response`，包含未被截断的三字段、任意响应附加字段、原分页与 `table_v2`，不存在逐计划请求。
- 计划 ID 不串 Seller／Advertiser；多身份相同 ID 不覆盖；迟到旧绑定不能冒充新作用域。
- 成功解析后新建、更新三字段；字符串 `"0"`／`"1"` 原样保存；未知字符串与 null 不当成关闭。
- 无效 JSON、空正文、超限、非零上游业务码、缺 table、坏行、重复 ID 全部非 2xx 且不部分写入；合法空列表不删除、不关闭。
- readwrite、readonly、未认证及错误 scope 的角色／授权行为；未授权正文不进专用日志。
- 同页重传不增加计划行；数据库失败回滚；日志失败不更新状态；成功状态只用 HTTP 判断。
- 请求正文日志原样可还原、仅专用文件、没有请求 headers 或 stdout 副本；文件权限与轮转检查覆盖有流量、无流量、旧日志淘汰；既有日志策略不变。
- 状态上传成功、超时、429／5xx、非重试 4xx、最大尝试数；失败不阻断发现与原统计流程；不从压缩日志重放响应、不因重试重取 TikTok。
- 不写 `ad_raw_log`／`plugin_logs`，不修改 daily/today 统计金额、日期或覆盖记录；原有广告 dumps 契约与测试继续通过。

ERP 验证仅通过 `bash scripts/test_isolated.sh fast <相关测试文件>` 和 `bash scripts/test_isolated.sh --refresh-template fast`；不得直接 pytest／alembic upgrade 或使用生产形数据库。插件遵循其 README 的 `npm test`、TypeScript、build 与必要 coverage 验证，均使用模拟网络。最终记录精确测试命令、提交与跨仓库版本，不以自动化模拟代替真实浏览器验证。

## 8. 部署顺序与剩余边界

先由人部署 ERP 并应用状态表迁移、确认专用日志与 timer，再发布／安装插件。旧插件仍可同步原统计；新插件遇到尚未部署的状态接口 404，应记录失败但原统计继续工作。回退插件无需删除状态表，表和历史日志不通过回退自动 DROP。

本方案不新增服务重启或生产迁移的 agent 执行授权。真实联调验收由人使用已绑定 Seller Center 会话触发计划发现，核验分页上传、状态原值与本机日志过期情况。

剩余限制：状态仅覆盖实际发现的计划；无采集时间不能抵御迟到旧响应；无批次无法判定全账户快照完整性；无持久重试队列不能保证 Worker 重启后的补传；按日轮转受 timer 运行影响。不得据此推断未出现计划已关闭，也不得用当前状态还原历史或决定 SPU 排除规则。
