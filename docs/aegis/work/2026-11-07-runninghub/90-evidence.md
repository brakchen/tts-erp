# 候选交接证据

基准：`aa795a4daa1a968be6c3fa6bd8a11bf5051be9ce`（2026-11-07 同步）；业务代码提交 `3b8074e5a02a0e7319671bdda68f612b418c4fed`，同步提交 `9a2b64b84cbc0e84d4ae061b01f8360c8be6c1e1`。作者上下文 `/root`；实际变更及最终身份由 `git diff --name-only <基准> HEAD` / `git rev-parse HEAD` 读取，不另建任务登记源。

## 已执行

- Ruff检查、TypeScript typecheck通过；没有新增Python/npm运行依赖。
- `FRONTEND_TEST_FILTER=src/pages/runninghub/index.test.tsx bash scripts/test_isolated.sh --refresh-template fast tests/api/test_runninghub.py tests/browser/test_runninghub_page.py tests/frontend/test_components.py --maxfail=1 --tb=short`：35个pytest检查通过；适配器内3个组件用例通过。日志：HP desk `/tmp/rh-final-targeted.log`。
- `bash scripts/test_isolated.sh api tests/api/test_runninghub.py -k "full_account or mock_official_chain or parallel_workers or balance_rejection" --tb=short`：最后的4项定向检查通过；包括账户配额互斥/互不阻塞、原任务本地恢复、凭据缺失时继续已下载成片处理。日志 `/tmp/rh-final-recovery.log`。
- `bash scripts/test_baseline.sh --scope auto --base aa795a4daa1a968be6c3fa6bd8a11bf5051be9ce --scenario page-assets --refresh-template --keep-log /tmp/rh-baseline.raw.log`：完整后端、页面壳/资源与前端回归已执行；前端52文件/761用例通过。仅页面固定清单与结构快照/head登记3项偏差，随后按测试指南仅对这3项复测并修复。原始日志 `/tmp/rh-baseline.raw.log`。
- `scripts/regen_schema.py --db-url postgresql://postgres@localhost/tts_erp_test_template`：只读schema生成，源已确认 `0074_runninghub`；新增9表，当前16个业务schema/87表。SQL未手改。
- `bash scripts/test_isolated.sh fast tests/api/test_frontend_pages.py::test_activation_matches_implemented_candidate_pages tests/publishing/test_final_v16_findings.py::test_architecture_inventory_is_derived_from_live_schema tests/publishing/test_final_v18_findings.py::test_generated_schema_and_architecture_cover_publishing_head --tb=short`：修复后3项全部通过，日志 `/tmp/rh-schema-fixed.log`。保留严格集合相等与结构相等断言。
- 实际编译页面在390/1440/2560宽度、两种URL前缀均通过键盘选择与溢出断言；三个宽度截图已人工查看。截图在任务校验checkout的 `frontend/.cache/screenshots/TEST_runninghub_*`。

## 覆盖与边界

mock HTTP覆盖官方两种响应包装、multipart、完整图JSON字符串、长ID、种子/尺寸连线、804/813不盲重提、超时/崩溃保留额度、查询错误不释放额度、下载/编码只恢复本地、失败新Attempt保留旧费用、余额/凭据错误的账户派发暂停。

真实隔离PostgreSQL覆盖多个Worker共享最多5个预留；真实FFmpeg/FFprobe覆盖竖屏选择（横向对比在第一项）、候选歧义、120帧/24fps/5秒及可选音轨；mock任务存储覆盖raw/final与六段ZIP。486任务/81组仅离线计划，不是生成量。

未执行真实RunningHub账户/生成/上传联调、真实对象存储端到端、生产迁移/部署/重启；不存在画质、实际费用单价或生产可用性验收声明。上线依照 `docs/api/runninghub.md` 由人工配置和迁移，真实批次需主动确认付费。最终合并前还须在锁内校验精确的 prospective master commit。

复杂度控制：协议/映射/计划/数据库领取/媒体/Worker/API各有一个所有者，复用既有凭据、MinIO和应用壳；保留历史Attempt和工作流快照是审计要求。未新增队列框架、并行状态通道或全局测试设施。较长的专属测试文件共享一套端到端夹具，覆盖28个有差异的后端验收；不以复制实现的断言替代真实数据库与媒体处理。仓库没有额外的Aegis复杂度硬限文件；以AGENTS.md和已执行验收为基准。
