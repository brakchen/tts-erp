# 数据库维护待办

> 本文记录已明确延后的数据库维护决策；不是可自动执行的清理计划。

## TODO 1：大表 retention

**状态：等待用户决策。**

当前生产基线（2026-09-30）：

| 表 | 行数 | 大小 |
| --- | ---: | ---: |
| `integration.raw_records` | 873,404 | 1.128 GiB |
| `plugin.intercepted_requests` | 1,265,553 | 1.004 GiB |
| `plugin.plugin_logs` | 199,592 | 0.681 GiB |
| `plugin.ad_raw_log` | 365,923 | 0.635 GiB |

待确定：热数据保留期、是否冷存储、按时间分区与否、删除前 FK/可重建性审计、定时任务和回滚方式。


## TODO 2：生产 dump 归档策略

**状态：等待用户决策。**

待确定：本地保留天数、异机或对象存储副本、checksum 清单、删除授权流程与定期 restore drill。

现有历史 dump 不应在该策略明确前自行删除。
