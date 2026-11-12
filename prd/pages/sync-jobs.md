# 定时任务

## 目的
查看同步任务健康度，按需手动触发一次，或由管理员启停周期调度。

## 角色
打开本页要求 **readwrite**。立即执行：readwrite / admin。启停周期：admin。停用周期不影响立即执行。预置只读分析不能进入。

## 主流程
1. 刷新并查看任务清单：周期、范围（系统级或 TikTok 已授权店）、状态、上次运行、落后周期。
2. readwrite 可立即执行；TikTok 类任务可选择单店或全部授权店铺，全店铺执行需二次确认。
3. admin 可启用或停用周期调度。

## 关键字段 / 操作
- 表列：任务、周期、范围、状态、上次运行、周期调度、立即执行。
- 当前注册表示例（周期以调度表为准）：tiktok.orders / order_detail / products / logistics / after_sales / finance，token.refresh，fx.sync，妙手相关采集与成本回填，reporting.cost_snapshots，reporting.profit_daily（旧版粗略毛利，不是 v10 真相源），spu.image_mirror，analytics.shop_fee_rate。

## 空态 / 错态
- 暂无注册任务。原执行店铺已不可用时重置为全部授权店铺。冷却期内读取和提交均暂停，不会自动重试。核对不能证明原命令已生效，重新执行可能重复上游同步。

## 验收要点
- 已停用的 `plugin.ad_merge_today2daily`、`analytics.spu_profit_deterioration_alert` 不再作为现行调度任务。
- 店铺授权和手动同步不是必须 admin（与部分旧注释不同）。

## 缺口
见总册第 7、8、14 条。

## 现状附录
- 路由：`/v2/pages/sync-jobs`。
