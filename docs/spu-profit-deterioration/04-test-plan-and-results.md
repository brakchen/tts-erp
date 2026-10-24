# SPU 盈利表现对比：验收矩阵与执行记录

> 2026-10-24 重建。权威为 [PRD v3](01-product-proposal.md) AC-001～023、[技术契约](02-technical-design.md)。本文件区分测试计划、已执行证据和未覆盖事项；旧告警测试绿色不代表新产品验收。

## 1. 安全与证据范围

所有测试只能经 `bash scripts/test_isolated.sh ...`，每次独占 `tts_erp_test_*` clone。禁止直接 pytest、共享/生产库、归档迁移套件或以全 skip 冒充通过。`bash scripts/test_baseline.sh` 按项目规则执行一次并与提交的 master 基线比较，新增失败只重跑最小 selection。

只读 source audit、mock browser、真实 SQL API、冷启动真实页面、构建和线上运行分别只证明其覆盖面。当前实现与部署必须分开；未获授权的生产 migration/restart/同步不在 agent 验证范围内。

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

实施后的 GREEN、SQL去重、browser结果、独立复审与baseline尚须追加；上述结果不代表本页已修好或部署完成。

## 5. 历史结果的处置

2026-10-07的 unit/browser/job/E2E 记录针对旧阈值告警。该版本的 shifted persistence、severity/sample gates、配置发布、stale→503和无效输入回滚断言不能继续作为本页验收权威。旧 evaluator/job 的独立内部测试可保留；本页 API/browser 测试改按本矩阵验证。原纹理/CSS字符串验收退休，改测文本/结构/键盘和实际状态，不反向恢复旧UI。

本页普通需求不新增试用人员、周期、主观评分或效果审批；功能实现后持续接受反馈，生产发布仍遵守正式流程。
