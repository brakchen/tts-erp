# 公共采集箱挂牌价查询

`procurement.procurement_products.source_unit_cost` / `source_min_unit_cost` / `source_max_unit_cost` 来自妙手公共采集箱的 1688 挂牌价。`miaoshou.common_collect_box` 定时同步这些字段；`miaoshou.sync_source_cost_to_master` 可将同一 `source_item_id` 的价格复制到 TikTok 侧采集行。这些字段用于货源参考和与采购成交价对比，**不是采购成本**。

TikTok SPU 的成交成本来源为当前人工成本 `procurement.manual_product_costs`，或已核实匹配店铺和 SPU 的 `miaoshou.purchase_prices`。旧 `SOURCE_PRICE` 快照只保留审计，不进入成本查询、覆盖率、日利润或 SPU ROI。

查询单个 SPU 的挂牌价时，先由 `commerce.products_spu` 的 `(shop_pk, spu_id)` 定位，再用 TikTok 侧 `procurement_products.external_product_id = spu_id` 取得 `source_item_id`，最后与公共采集箱记录按 `source_item_id` 对比。查询采购成交价则以 `miaoshou.purchase_prices` 的 `(shop_pk, spu_id)` 匹配，要求 `resolution_status='matched_shop'`、`currency='CNY'`、正单价和有效成交时间。相同成交时间出现不同单价时不自动选取。

2026-11-24 生产只读核验：39 个可配对的在售 SPU 中，32 个成交价低于挂牌价、4 个高于、3 个相等；成交价相对挂牌价的中位差为 −13.16%。挂牌价采集时间与采购成交时间并不完全一致，此对比用于检验口径，不能作为同一时点的价格审计。Ridge Little Shop 缺 TikTok 侧 offer 映射，当前无法做同样的逐 SPU 配对。
