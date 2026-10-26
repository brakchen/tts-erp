# SPU 盈利表现对比：验收矩阵与执行记录

> 2026-10-24 重建。权威为 [PRD v3](01-product-proposal.md) AC-001～023、[技术契约](02-technical-design.md)。本文件区分测试计划、已执行证据和未覆盖事项；旧告警测试绿色不代表新产品验收。

## 1. 安全与证据范围

所有测试只能经 `bash scripts/test_isolated.sh ...`，每次独占 `tts_erp_test_*` clone。禁止直接 pytest、共享/生产库、归档迁移套件或以全 skip 冒充通过。`bash scripts/test_baseline.sh` 按项目规则执行一次并与提交的 master 基线比较，新增失败只重跑最小 selection。

只读 source audit、mock browser、真实 SQL API、冷启动真实页面、构建和线上运行分别只证明其覆盖面。当前实现与部署必须分开；未获授权的生产 migration/restart/同步不在 agent 验证范围内。

### 2026-10-27：现网 legacy 页面布局修复

现场仍使用 legacy 模板与脚本。自动表格布局把大量宽度分给含长缺失原因的 ROI 列，
其他列被挤压；全部来源限制默认展开，又把列表推到首屏以下。
本次仅调整专属 CSS 和模板：筛选区按网格对齐、统计提前且等宽排列、来源限制使用原生
`details` 展开、桌面表格固定列宽并允许长内容换行；窄屏继续使用原有完整指标卡片。
不改变数据接口、盈利计算、源数据异常处理、权限或 React 页面启用范围。

- 代码提交：`6767761e150702d528574ff2dd45f0264ee56f45`；源 master：
  `362fce770a7e4b033d0afa01a5dcab0112f24570`。
- `ruff check tests/browser/test_spu_deterioration_alert_page.py`：通过。
- `bash scripts/test_isolated.sh browser tests/browser/test_spu_deterioration_alert_page.py`：
  exit 0；独立 clone `tts_erp_test__schan_20261026155624_2816062_dd1f0acd` 已清理。
  新增 390/1440/2560px 长缺失原因回归，核对列宽比例、筛选区对齐、统计位置、说明区键盘展开
  与收起及整页不溢出；已有筛选、错误恢复、明细和键盘测试同时通过。
- 已人工查看隔离模板在 390/1440/2560px 的当前截图；也在单独浏览器预览页替换候选 CSS 和
  对应模板结构，检查现场长原因和来源限制。浏览器预览不代表线上文件已发布。
- 完整基线验证和发布结果在取得执行证据后补记；本段不声称已上线。

## 2. 验收矩阵（目标断言）

| AC | 最小覆盖 | 所有者/场景 |
| --- | --- | --- |
| 001 | 默认唯一 7 天、可切 3 天、准确两段当地日期，无 1/全部 | API + browser |
| 002 | 精确范围绕过活动排除，仍只查同店指定 SPU 并解释限制 | SQL API + browser |
| 003 | 日历 max、链接/手输/API 越界 422 保留输入，非法 IDs/window 不扩范围 | 日期 resolver + API + browser |
| 004 | 改条件重置第一页/关闭明细；合法 URL 恢复；3/7 固定同一活动范围 | SQL API + browser |
| 005 | ROI 2→2.5 与利润100→80方向独立，差额+0.5/−20，展示百分比+25%/−20% | browser |
| 006 | ROI−0.5→−0.2原值和+0.3保留；非正上期只抑制百分比 | API + browser |
| 007 | 真实0、负数、零广告费ROI无解、必要事实缺失可区分；其他指标仍可读 | canonical + API + browser |
| 008 | 展示舍入不改变资格、标记、业务总数；日期/CNY/估值说明可见 | SQL + browser |
| 009 | 0/1/2累计单但近期零单正广告费仍入范围及统计；显示实际盈利和事实重点标注 | SQL API + browser |
| 010 | 仅上期标注对应上期；有单、仅负盈利或未知订单不误标 | domain/API + browser |
| 011 | 3天波动说明；缺失不当零；不输出加投停投建议 | API + browser |
| 012 | 3/7方向可相反；没有severity/state/history-confirmation/threshold流程 | page shell + API + browser |
| 013 | 同一订单包含两个SPU，期订单只计1、SPU计2；无告警数量 | 真实SQL API |
| 014 | 翻页不改完整范围统计；切窗口改期订单统计而不改活动范围 | 真实SQL API |
| 015 | totals不可用有原因、不是旧数/假0、可重试 | API + browser失败 |
| 016 | 内部PK不同业务ID仍正确跳同店SPU/经营日期；可返回原查询 | API URL + browser真实导航 |
| 017 | Tab/Enter打开关闭明细、焦点返回、进入原生详情链接；桌面/窄屏 | browser键盘，不用click代替 |
| 018 | 403与服务失败分别说明，明细不串SPU、可返回 | auth API + browser |
| 019 | A延迟→B成功或失败→A迟到均不覆盖；B加载/失败清掉A列表/统计/详情 | browser确定性gate |
| 020 | 真空结果回显条件可调整；零单正消耗不是错误空结果 | API + browser |
| 021 | 缺必要依据/事实/服务失败非2xx或明确对应不可用，不以成功空列表遮掩；请求编号/重试 | API真实错误 + browser |
| 022 | 各必要来源保守交集日期；旧结果可读、实际数据日与计算时间分开；刷新不推进源日期 | resolver SQL/API + browser |
| 023 | 匿名401/页面无权403、readonly可读，不能退匿名；保留现有页面权限点 | enforce auth API/冷启动browser |

## 3. 来源日期专项

- 订单/售后/财务按店归因成功作业、零报告失败、开始/结束时刻分开；开始日减一是保守策略，不称 persisted query-through。
- 游标是最新观察的 update-time，不是完整采集日期；作业成功也可能有未解决订单行/关联问题，需单独检查。
- 广告 seller/advertiser/campaign/day 观测不是 campaign发现/全部页证明；明确 `coverage.status=limited` 与已知缺口，成功空响应与没有采集分开。
- 日期 envelope 固定14天，不因切换7/3而悄悄推进上限/换SPU。
- 必要来源缺失、已知缺口影响指标、跨当地午夜、未来事实、后续失败但旧成功仍可读、真实零值分别测试。
- 不将“没有完备manifest”扩成新产品审批门槛；也不把笼统limited标签用来掩盖假零或错误日期上限。

## 4. 本次已执行记录

| 阶段/命令 | 实际结果与范围 |
| --- | --- |
| 三路只读 audit | 后端、UI、AC覆盖三个subagent完成；发现activity=undefined导致422、旧告警契约不符、错误订单统计/详情ID/加载残留等；无运行时通过结论 |
| `bash scripts/envsetup/install-test-deps.sh --check` | exit0，测试模板/客户端/venv就绪 |
| 首次隔离API诊断 | wrapper成功创建/清理clone，但新worktree缺frontend toolchain；未执行测试，不称RED |
| `npm ci --include=dev --prefix frontend` | worktree独立依赖准备：176 packages，0 vulnerabilities；不是应用验收 |
| `bash scripts/test_isolated.sh api tests/api/test_spu_deterioration_alert_api.py`（旧实现） | 15 passed；仅证明旧告警测试，不能作为v3验收。日志 `/tmp/spu-compare-initial-api.log` |
| `bash scripts/test_isolated.sh api tests/api/test_pages.py -k spu_profit_deterioration`（新shell断言/源码未改） | 严格RED：3 failed、3 passed；失败分别为旧settings仍存在、额外1/全部窗口、旧config写路径仍存在；日志 `/tmp/spu-compare-shell-red.log` |

### 4.1 集成后功能证据（代码 HEAD `df19d0140beb6242236da7fd12309ad3254be555`）

| 检查 | 实际结果 |
| --- | --- |
| `bash scripts/test_isolated.sh fast tests/analytics/test_spu_deterioration_alert.py tests/analytics/test_spu_deterioration_comparison.py tests/analytics/test_spu_profitability_formula.py tests/api/test_spu_deterioration_alert_api.py tests/api/test_pages.py tests/browser/test_spu_deterioration_alert_page.py tests/browser/test_spu_comparison_drilldown_dates.py tests/browser/test_spu_date_range.py -o addopts=-ra --tb=short` | **177 passed，0 failed，0 skipped**；127.26s。分解：旧 evaluator15、比较领域8、canonical公式26、比较API46、页面API25、比较browser48、新日期browser7、原日期guard2。一个既有 Starlette/httpx 弃用 warning，不作为失败。 |
| `bash scripts/test_isolated.sh e2e tests/e2e/test_spu_deterioration_alert_smoke.py -o addopts=-ra --tb=short`（最终完整运行） | **5 passed，0 failed，0 skipped**；27.18s。当前隔离 clone + 冷启真实 API + Chromium；覆盖带前缀 cookie 桌面1440、API-key手机390、真实业务值/旧结果/日期上限/7→3/去重统计、参数和匿名边界、键盘详情正确业务ID及经营日期/浏览器返回。不是常驻 :9877 的部署证明。 |
| UI 独立复审 | fresh reviewer读取完整提交范围索引、相关实际 diff/当前代码、177/5 XML及390/1440/2560截图：未发现问题，`OK with notes`，仅保留完整baseline门槛。 |
| 主动 LSP probe（13条实际改动路径） | 0 reported errors；3 confirmed clean，10 push-only/silent unconfirmed。不声称13条全部完成静态类型证明。Ruff/Node语法/编译检查及runner前端typecheck/build另已通过。 |

**验收夹具纠正，不改业务规则：**首轮真实 E2E 为3通过2失败。内置 viewer 并不包含此页面权限，改为显式授权 comparison/ROI 页面、API层级为 readonly 的 TEST 角色后，失败用例最小重跑2/2通过，再完整5/5通过。无页面权限的 HTML 仍403；数据API沿用既有全局readonly层级（`accounts.pages.required_page_permission` 与 `service._build_context`），没有借修页面改成另一份授权矩阵。

独立查验 canonical `calculate()`：真实零单投放样本消耗49 CNY、净利润−49 CNY、ROI **0**（ROI分子不减广告费），修正父验收曾误写的−1预期；另有已确认退款场景验证可计算负ROI正常保留，未修改统一公式。

后端已验证未知商品/金额/订单行/关联、空广告金额、不归属店铺或畸形issue不冒充零；全范围订单跨SPU/分页/批次去重；金融读取仅计算当前页，存在性查询不随历史订单增长，原始广告响应不整批物化。日期下溢422、旧成功晚于新失败仍可读、3650天前有效空广告观察等均有回归。`coverage=limited`披露具体观测与限制，不认证所有上游campaign/页采集完整，也未做生产规模延迟承诺。

共享布局夹具另行发现并修复：`tests/browser/conftest.py` 仍返回旧告警shape，导致共享字体/溢出巡检实际只看错误态。新增桌面/手机“必须ready且有真实比较行”的回归，先观察 **2个预期RED**，再仅更新SPU canned payload并退休无消费者的旧阈值helpers；`shared_layout_fixture` 与关联字体/溢出选择 **8 passed、0 failed**。未改公共server/renderer或其他页面mock；mock只证明布局，不代替真实SQL/E2E。

**独立后端复审闭环：两条 P1 已解除。**初轮发现上述177/5未覆盖的路径：NULL订单状态假零/假无单投放；未结算完成退款的必要金额缺失仍输出数值盈利。`spu-compare-missing-facts` 先实际最小 **2 RED**、变体 **12 RED/17 controls GREEN**，再在订单事实检查与canonical完整性出口修复；保留其他可用事实，不改公式。随后补充已结算邻居，观察 **7 RED/7 controls GREEN**，证实实际ROI与净利润均不依赖已结算订单这笔售后金额，最小收窄到必要未结算退款；不复制预测或盈利公式。真实 `SETTLEMENT` 为0、正数、多个分量相抵为0均可保留；只有fee/refund组件、空交易、同SPU混合结算/未结算时不假定已结算。数据库非空约束也在隔离库实查。

最终子分支 `7214b178b653135bbd4e4f02a7ea9d39b07fbbb3`：**95比较API +34领域/公式 GREEN，0 failed/skip**。原独立backend reviewer针对最终提交范围复核、核对canonical源查询/实际公式和隔离日志：**No issues found，两个原P1 discharged，code OK with notes**；未把代码复审当全体验收。parent已集成 `763c10b0b8946233497d85241aec0f64e4875320`，再次独立执行完整比较API **95 passed/48.41s** 与真正冷启动全栈 **5 passed/36.98s**，均0 failed/skip；对应外部artifacts为 `missing-facts-parent.xml`、`cold-e2e-post-review.xml`。4条最后改动路径主动LSP再次0 reported errors但全部silent/unconfirmed；不声称有完整类型证明，Ruff/format/compile与测试另有通过证据。整段日志保留初轮夹具精度断言纠正：测试应比较既有HTTP两位HALF_UP串，不拿原始高精度Decimal直接作wire预期；生产精度/公式未为此改动。

完整fast基线首轮已在 `c3a57da7851f7c0fb4396cbc919cda85b33fa69b` 执行，原始机械diff为13项对零基线，**未当作通过**：12项是另一未合并lane通过官方runner把默认模板升级到 `0070_monitor_health`，本源head为0069造成的迁移解析/schema清单错误；1项是旧runtime-config测试仍要求坏告警策略阻断新比较API。先依规依赖体检并与模板升级owner协调，再用官方 `--refresh-template` 恢复默认0069，最小复验得到 **21 passed、仅旧守卫1 RED**。保留旧内部 `effective_config_details` 的坏配置失败关闭守卫，另以有效比较数据证明坏旧配置不改变事实/统计；没有恢复新页对旧告警策略的依赖。随后使用官方 `--template-db tts_erp_test_template_spu_compare_0069` 独占模板，runtime-config全文件、新比较独立性回归、价格采集全文件及两个schema/head守卫 **35 passed、0 failed**，所有原13失败均已分类并针对性解除。没有更新零基线或反复全套重跑掩盖信号；最后精确prospective master必须另执行规定的完整基线。上述修正仅测试/文档，生产实现保持基线运行时的代码。

**最终交付门槛：**完整fast基线、精确prospective master验证、推送与board收口，必须按实际提交执行；最新执行SHA、原始日志、基线机械diff与最终结果记在canonical board的 `spu-compare-delivery` 线程，不在本固定功能证据段另维护实时状态。该段提交前已显式fetch远端master并同步 `4bcef08df77172b6cb48007267d3927e4078ca19`；若master再前进，须再次同步和验证。上述功能证据不代替最终门槛，也不是生产部署声明。

## 5. 历史结果的处置

2026-10-07的 unit/browser/job/E2E 记录针对旧阈值告警。该版本的 shifted persistence、severity/sample gates、配置发布、stale→503和无效输入回滚断言不能继续作为本页验收权威。旧 evaluator/job 的独立内部测试可保留；本页 API/browser 测试改按本矩阵验证。原纹理/CSS字符串验收退休，改测文本/结构/键盘和实际状态，不反向恢复旧UI。

本页普通需求不新增试用人员、周期、主观评分或效果审批；功能实现后持续接受反馈，生产发布仍遵守正式流程。
