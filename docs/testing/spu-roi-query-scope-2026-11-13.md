# SPU ROI 销售与价格查询优化验证

日期：2026-11-13。原版 SQL：`ce7a92e85207f74644c4b0e60eaed3343767b056`。
性能测量候选：`220cb745`（后续提交仅追加本验证记录）。

本次仅调整销售订单范围与价格观察范围/聚合复用，不改变金融公式、字段、
经营日期、预测日期、最新观察选择规则或数据持久化。未新增索引、执行生产迁移或发布。

## 改动

- 销售先用所选 SPU、已付款状态、`COALESCE(order_time, paid_at)` 经营窗口
  限定订单，再读取结算、整单 GMV 和确认退款；末层通过 GMV 关系复用订单范围。
  分母仍含其他 SPU 与未归属商品行，结算和退款证据不按自身日期裁剪。
- 价格先限定当前订单行，通过 `(shop_pk, order_pk, external_line_id)` 获取全部
  观察版本，保留原版本时间/采集时间/hash 排序。先取 canonical，再判断价格有效性；
  无观察及未付款等排除项继续进入覆盖率。覆盖率和价格分桶共用一次物化 population，
  两条聚合 SQL 合为一条，金额保持 PostgreSQL numeric / Python Decimal。

## 一致性

原 SQL 固化在 `tests/fixtures/spu_roi_query_scope_baseline.json`，来源 SHA 写在 fixture 内。
`tests/api/test_spu_roi_query_scope.py` 在同一隔离临时库比较旧、新 SQL；完整 API 对照
固定计算时间，比较全部 JSON 字段，不排除 metadata 或金额字段。

- 销售五种窗口：无边界、仅开始、仅结束、单日、空窗口。覆盖时间回退、双 NULL、
  非已付款状态、跨 SPU/未归属行分摊、多笔/零结算、晚到确认退款。
- 价格三种选择：单 SPU、多个 SPU、空选择。覆盖观察旧/NULL SPU 映射、缺观察、
  经营窗口之后的最新观察、最新缺价覆盖旧价、同版本/采集时间的 hash 决胜、
  NULL 版本时间、同 line ID 不同订单/店铺，以及各类状态覆盖率。
- 两组完整 API 响应逐字段一致；每次响应仅执行一次销售 SQL、一次价格聚合 SQL，
  保留表存在性探测。
- 三个性能场景均先比较全部 SQL 结果，再计时；无差异。

最终差分与性能模块共 10 项通过。此前现有 `test_spu_roi_api.py`、
`test_spu_price_stats.py`、`test_spu_roi_query_efficiency.py` 全部通过。
最终提交的仓库自动选择回归另由 `scripts/test_baseline.sh --scope auto` 验证，
其执行日志是最终提交是否通过的依据。

## 性能

PostgreSQL 18.4、Python 3.14.4；通过隔离入口克隆 schema-only 测试模板，
合成 20 个 SPU、10,000 个历史订单、200 个当前订单、30,600 条价格观察、
约 5,100 笔结算。未复制生产数据。造数后 ANALYZE，规划器参数保持默认。

每条旧/新查询各预热一次，再交替顺序测五轮，取中位数。计时包含 SQL 执行和
结果读取；旧价格耗时包含原覆盖率与分桶两条语句。以下不是页面端到端耗时。

| 场景 | 销售原版 → 新版（ms） | 销售耗时降低 | 价格原版 → 新版（ms） | 价格耗时降低 |
| --- | ---: | ---: | ---: | ---: |
| 单 SPU，近 7 天 | 5.690 → 3.099 | 45.5% | 69.390 → 2.946 | 95.8% |
| 全店，近 30 天 | 22.513 → 4.766 | 78.8% | 60.900 → 4.750 | 92.2% |
| 全店，不限日期 | 36.902 → 35.198 | 4.6% | 122.842 → 76.626 | 37.6% |

全店不限日期的销售收益很小，不应外推为稳定的同比例提速。数据量、分布、缓存、
并发和规划器都会影响生产效果；默认回归不设置容易抖动的耗时断言。

`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON, TIMING OFF)` 同时保存：
全店近 30 天销售 shared hit blocks 从 4,202 降至 2,625；价格两条旧语句合计
7,168 降至 1,582。全店不限日期的新价格查询产生 668 个 temp written blocks，
这反映物化的内存/临时文件代价；本次没有为性能测试调大 work_mem。

五轮原始时间与计划摘要见 [JSON 记录](spu-roi-query-scope-2026-11-13.json)，
完整计划见 [gzip JSON](spu-roi-query-scope-2026-11-13.plans.json.gz)。

## 复现

```bash
# 正常差分回归，不运行 slow 性能用例
bash scripts/test_isolated.sh api tests/api/test_spu_roi_query_scope.py

# 显式包含合成数据性能验证；输出报告路径由 tmp_path 生成
bash scripts/test_isolated.sh api tests/api/test_spu_roi_query_scope.py \
  -m 'domain_api and not domain_migration' -s

# 已提交候选的仓库规定回归；base 为准确同步的 master SHA
bash scripts/test_baseline.sh --scope auto \
  --base ce7a92e85207f74644c4b0e60eaed3343767b056
```

测试机原 Node 22.22.1 触发仓库版本闸，未执行应用测试；使用经官方 SHA256
校验的任务级 Node 22.22.2 后重新执行。未变更系统 Node 或生产服务。
所有实际测试都由隔离入口创建独占 `tts_erp_test_*` clone，并在结束时清理。
