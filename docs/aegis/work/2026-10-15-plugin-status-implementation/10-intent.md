# 通用插件状态与单目标恢复实施 - Intent

## TaskIntentDraft

- Requested outcome（2026-10-22更新）: 连续完成当前owner的十五个open rows，包括状态/单目标恢复与两端配置同步；Parent直接实现、验证、Git，不逐原子段等待继续确认。
- Goal: 原已批准状态/绑定协议与配置同步目标完整可验证交付；只读独立reviewer提供advisory，不恢复旧writer代理，不将局部通过当整体完成。
- Success evidence:
- S01-S14映射+isolated目标与baseline+插件完整测试覆盖tsc/build+owner spec/quality review
- Stop condition: 全部代码与可运行验收通过才交付；外部授权或真实环境证据缺失如实标记blocked/needs-verification
- Non-goals:
- 多店并行、通用远程执行平台、跨插件后台依赖
- Scope: ERP plugin/status及独立monitor插件，用户已扩展包含两端monitor-config rollout；共享core仍需明确未来写队列协调，旧工作树和foreign WIP不改不暂存。
- Verification boundary: 用户已明确批准仅从已提交版本复制两个 TS fixture 至隔离目录加载；原件/元数据与两份 foreign JSON 仍不读改。较早间接 import 不被追认。当前验证/开发移至独立 sparse worktree，trace 导出写 OS temp 或显式 evidence 目录，不默认覆盖 Git fixture。
- Change kinds:
- feature
- Risk hints:
- high：权限/持久化/恢复与真实关停门禁

## BaselineReadSetHint

- docs/plugins/plugin-status-report.md@fb42e16

## BaselineUsageDraft

- Required baseline refs:
- docs/plugins/plugin-status-report.md@fb42e16
- Acknowledged before plan:
- none
- Cited in plan:
- docs/plugins/plugin-status-report.md@fb42e16（原实施计划Baseline/Authority已引用）
- Current readback (2026-10-16 batch4): 完整重读当前方案§1–17、原计划、checkpoint/resume hint、插件AGENTS及当前canonical registry；历史before-plan字段初建未填，不追写成当时已有确认
- Missing current refs:
- none
- Advisory decision: continue within current fifteen-row scope;原15行仍4完成/11open。上游已到ff4b17f/0.4.28，当前候选e35c4e3仍0.4.27、未整合ff4b/0.4.28及peer报告的3abed5c/v0.4.29 UI分支。0c6f四finding实际typed复现后1c修，9d/7e/b17/6086已将本地Runtime/Controller/native/terminal owned metadata及迟到Promise接通；明确注入caps/pages，12相关498PASS/1旧lateclearFAIL/type0。e35新增realstorage clear1业务RED/type0，非ready；新423c/54ae只设计actorless PRE→OWNpost→actualclear。旧95/30迁移、clear/ACK/capture/firstprotected/actualcomposition等仍开，不能外推完整physical/配置/产品完成。原 safety/ownership/test gates unchanged. 当前readback/evidence见20-checkpoint和resume-state-hint.currentExecution；原MINIMAX分工/未回交等段落仅历史，不覆盖Parent直接执行与已完成显式handoff。

## ImpactStatementDraft

- Compatibility boundary: 单Seller/Advertiser；保留原业务门禁和配置隔离；不执行生产迁移/重启/真实写请求
- Affected layers:
- API/database/worker/browser-extension
- Owners:
- 01a1406d-2041-7452-8338-3d7de574337f
- Invariants:
- 失效先安全暂停；只有服务端授权且完整新证据才能恢复
- Non-goals:
- 多店并行、通用远程执行平台、跨插件后台依赖

These records are Method Pack drafts / hints, not authoritative runtime decisions.
