# Monitor 接口原始抓包（仅凭证脱敏）

来源：本次用户提供的三条 cURL 与对应响应；2026-10-14 归档。
本文只替换 Cookie 参数整值、CSRF/Token/Authorization 等认证头值；其余用户原文逐字符保留，包含所有非凭证请求头、URL/query、完整请求体、完整响应、提示文字和空白。不省略表格行，不更改 ID、金额、时间、User-Agent、Referer 或嵌套 JSON 字符串。

`[REDACTED_COOKIE]` / `[REDACTED_CREDENTIAL]` 为不可用占位符。本文用于离线协议分析，**不得直接执行**；没有调用任何广告开关接口。原凭证不写入本文或插件仓库。

兼容设计见 [02-technical-design.md](02-technical-design.md)。状态接口的 operation=1 仅存档，不代表开发自动开启功能。


## 广告计划列表 v2：完整用户原文

```text
curl 'https://seller.tiktokshopglobalselling.com/oec_ads/shopping/v1/oec/stat/post_campaign_list_v2?aadvid=7694121351103168530&language=zh&locale=zh&oec_seller_id=7494961084692727148' \
  -H 'Accept: */*' \
  -H 'Accept-Language: zh-HK,zh-TW;q=0.9,zh;q=0.8' \
  -H 'Connection: keep-alive' \
  -H 'Content-Type: application/json; charset=utf-8' \
  -b '[REDACTED_COOKIE]' \
  -H 'Origin: https://seller.tiktokshopglobalselling.com' \
  -H 'Referer: https://seller.tiktokshopglobalselling.com/ads-creation/dashboard?mpa=1&activated_tab_id=0' \
  -H 'Sec-Fetch-Dest: empty' \
  -H 'Sec-Fetch-Mode: cors' \
  -H 'Sec-Fetch-Site: same-origin' \
  -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36' \
  -H 'X-CSRFToken: [REDACTED_CREDENTIAL]' \
  -H 'sec-ch-ua: "Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "Windows"' \
  --data-raw '{"query_list":["campaign_eligible_reject_reason","campaign_eligible_roi","campaign_opt_status","campaign_name","gmv_max_shop_id","campaign_primary_status","campaign_status","campaign_target_roi_budget","campaign_budget","cost","qcpx_coupon_cost","compensation_info","campaign_eligible_status","gmax_advanced_mode","current_optimization_modes","template_ad_schedule_type","campaign_additional_budget","template_ad_roas_bid","campaign_budget_mode","campaign_create_channel","has_active_creation","template_ad_start_time","template_ad_end_time","billed_cost","auto_increase_budget_effective_budget","campaign_budget_utilization_status","campaign_budget_utilization_today_threshold","campaign_budget_utilization_yesterday_threshold","onsite_roi2_shopping_sku","cost_per_onsite_roi2_shopping_sku","onsite_roi2_shopping_value","onsite_roi2_shopping","shop_roi2_qcpx_shopping_value","shop_roi2_qcpx_spillover_shopping_value","basic_cost","basic_onsite_roi2_shopping","creative_nobid_cost","session_info","gmv_max_bid_type","campaign_target_roi_budget_mode","promotion_days_mode","in_promotion_days","promotion_days_roas_bid_multiplier","promotion_days_budget_multiplier","promotion_days_schedules","pricing_value","qcpx_setting_is_enabled","all_boost_cost"],"start_time":"2026-10-07","end_time":"2026-10-14","page":1,"page_size":10,"dims":["campaign_id"],"order_field":"cost","order_type":1,"filters":[{"field":"external_type","filter_type":0,"in_field_values":["304","307"]},{"field":"campaign_status","filter_type":0,"in_field_values":["no_delete"]},{"field":"gmv_roi_mode","in_field_values":["2"],"filter_type":10}]}'  这是广告插件请求广告列表的新接口post_campaign_list_v2 ，之前是 post_campaign_list。这是 post_campaign_list_v2  的response：{
    "code": 0,
    "msg": "",
    "extra": null,
    "data": {
        "pagination": {
            "page": 1,
            "page_count": 2,
            "total_count": 11
        },
        "table_v2": [
            [
                {
                    "name": "campaign_id",
                    "data": "1878948103287058"
                },
                {
                    "name": "cost",
                    "data": "13.19"
                },
                {
                    "name": "pricing_value",
                    "data": "9"
                },
                {
                    "name": "promotion_days_budget_multiplier",
                    "data": "50"
                },
                {
                    "name": "all_boost_cost",
                    "data": "0.00"
                },
                {
                    "name": "campaign_eligible_reject_reason",
                    "data": "0"
                },
                {
                    "name": "campaign_budget_utilization_today_threshold",
                    "data": "0"
                },
                {
                    "name": "template_ad_end_time",
                    "data": "2036-10-10 22:19:28"
                },
                {
                    "name": "campaign_name",
                    "data": "商品 GMV Max_总收入_Ridge Little Shop_20261013221928"
                },
                {
                    "name": "campaign_eligible_roi",
                    "data": "0"
                },
                {
                    "name": "campaign_eligible_status",
                    "data": "0"
                },
                {
                    "name": "qcpx_setting_is_enabled",
                    "data": "false"
                },
                {
                    "name": "has_active_creation",
                    "data": "0"
                },
                {
                    "name": "shop_roi2_qcpx_spillover_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "onsite_roi2_shopping",
                    "data": "1.89"
                },
                {
                    "name": "auto_increase_budget_effective_budget",
                    "data": "200.00"
                },
                {
                    "name": "qcpx_coupon_cost",
                    "data": "0.00"
                },
                {
                    "name": "basic_onsite_roi2_shopping",
                    "data": "1.89"
                },
                {
                    "name": "campaign_create_channel",
                    "data": "63"
                },
                {
                    "name": "campaign_status",
                    "data": "roi2_mutex_exclusive_product"
                },
                {
                    "name": "campaign_opt_status",
                    "data": "1"
                },
                {
                    "name": "billed_cost",
                    "data": "11.82"
                },
                {
                    "name": "campaign_target_roi_budget_mode",
                    "data": "0"
                },
                {
                    "name": "template_ad_schedule_type",
                    "data": "持续投放"
                },
                {
                    "name": "basic_cost",
                    "data": "13.19"
                },
                {
                    "name": "in_promotion_days",
                    "data": "0"
                },
                {
                    "name": "promotion_days_mode",
                    "data": "1"
                },
                {
                    "name": "campaign_additional_budget",
                    "data": "0"
                },
                {
                    "name": "gmv_max_bid_type",
                    "data": "1"
                },
                {
                    "name": "gmax_advanced_mode",
                    "data": "1"
                },
                {
                    "name": "onsite_roi2_shopping_sku",
                    "data": "1"
                },
                {
                    "name": "campaign_primary_status",
                    "data": "disable"
                },
                {
                    "name": "campaign_target_roi_budget",
                    "data": "200.00"
                },
                {
                    "name": "campaign_budget_mode",
                    "data": "0"
                },
                {
                    "name": "template_ad_roas_bid",
                    "data": "3.20"
                },
                {
                    "name": "onsite_roi2_shopping_value",
                    "data": "24.98"
                },
                {
                    "name": "template_ad_start_time",
                    "data": "2026-10-13 22:19:28"
                },
                {
                    "name": "promotion_days_roas_bid_multiplier",
                    "data": "10"
                },
                {
                    "name": "current_optimization_modes",
                    "data": "[1]"
                },
                {
                    "name": "gmv_max_shop_id",
                    "data": "7494961084692727148"
                },
                {
                    "name": "cost_per_onsite_roi2_shopping_sku",
                    "data": "13.19"
                },
                {
                    "name": "shop_roi2_qcpx_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "campaign_budget",
                    "data": "200.00"
                },
                {
                    "name": "creative_nobid_cost",
                    "data": "0.00"
                },
                {
                    "name": "campaign_budget_utilization_yesterday_threshold",
                    "data": "0"
                },
                {
                    "name": "campaign_budget_utilization_status",
                    "data": "0"
                },
                {
                    "name": "compensation_info",
                    "data": "{\"query_date\":\"20261014\",\"compensation_status\":3,\"invalid_reasons\":[5],\"end_reasons\":[],\"is_success\":true,\"compensation_amount\":\"\",\"currency\":\"\",\"url\":\"\",\"product_id\":\"\",\"is_within_learning_phase\":false,\"compensate_category\":0}"
                },
                {
                    "name": "promotion_days_schedules",
                    "data": "[{\"start_time\":\"2026-01-24\",\"end_time\":\"2026-02-02\"},{\"start_time\":\"2026-03-01\",\"end_time\":\"2026-03-03\"},{\"start_time\":\"2026-03-24\",\"end_time\":\"2026-03-26\"},{\"start_time\":\"2026-04-01\",\"end_time\":\"2026-04-04\"},{\"start_time\":\"2026-04-24\",\"end_time\":\"2026-04-25\"},{\"start_time\":\"2026-05-03\",\"end_time\":\"2026-05-07\"},{\"start_time\":\"2026-05-24\",\"end_time\":\"2026-05-25\"},{\"start_time\":\"2026-05-30\",\"end_time\":\"2026-06-06\"},{\"start_time\":\"2026-06-24\",\"end_time\":\"2026-06-25\"},{\"start_time\":\"2026-07-04\",\"end_time\":\"2026-07-07\"},{\"start_time\":\"2026-07-24\",\"end_time\":\"2026-07-25\"},{\"start_time\":\"2026-08-04\",\"end_time\":\"2026-08-08\"},{\"start_time\":\"2026-08-24\",\"end_time\":\"2026-08-25\"},{\"start_time\":\"2026-09-03\",\"end_time\":\"2026-09-10\"},{\"start_time\":\"2026-09-24\",\"end_time\":\"2026-09-25\"},{\"start_time\":\"2026-10-07\",\"end_time\":\"2026-10-10\"},{\"start_time\":\"2026-10-24\",\"end_time\":\"2026-10-25\"}]"
                }
            ],
            [
                {
                    "name": "campaign_target_roi_budget_mode",
                    "data": "0"
                },
                {
                    "name": "creative_nobid_cost",
                    "data": "0.00"
                },
                {
                    "name": "onsite_roi2_shopping",
                    "data": "2.28"
                },
                {
                    "name": "campaign_budget",
                    "data": "200.00"
                },
                {
                    "name": "promotion_days_budget_multiplier",
                    "data": "50"
                },
                {
                    "name": "gmax_advanced_mode",
                    "data": "1"
                },
                {
                    "name": "current_optimization_modes",
                    "data": "[1]"
                },
                {
                    "name": "campaign_primary_status",
                    "data": "disable"
                },
                {
                    "name": "qcpx_setting_is_enabled",
                    "data": "false"
                },
                {
                    "name": "gmv_max_bid_type",
                    "data": "1"
                },
                {
                    "name": "campaign_budget_utilization_yesterday_threshold",
                    "data": "0"
                },
                {
                    "name": "basic_onsite_roi2_shopping",
                    "data": "2.28"
                },
                {
                    "name": "cost",
                    "data": "8.53"
                },
                {
                    "name": "campaign_budget_utilization_today_threshold",
                    "data": "0"
                },
                {
                    "name": "shop_roi2_qcpx_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "template_ad_schedule_type",
                    "data": "持续投放"
                },
                {
                    "name": "auto_increase_budget_effective_budget",
                    "data": "200.00"
                },
                {
                    "name": "template_ad_start_time",
                    "data": "2026-10-11 23:08:01"
                },
                {
                    "name": "campaign_target_roi_budget",
                    "data": "200.00"
                },
                {
                    "name": "template_ad_end_time",
                    "data": "2036-10-08 23:08:01"
                },
                {
                    "name": "qcpx_coupon_cost",
                    "data": "0.00"
                },
                {
                    "name": "promotion_days_mode",
                    "data": "1"
                },
                {
                    "name": "billed_cost",
                    "data": "8.08"
                },
                {
                    "name": "campaign_status",
                    "data": "roi2_mutex_exclusive_product"
                },
                {
                    "name": "campaign_eligible_reject_reason",
                    "data": "0"
                },
                {
                    "name": "onsite_roi2_shopping_value",
                    "data": "19.47"
                },
                {
                    "name": "all_boost_cost",
                    "data": "0.00"
                },
                {
                    "name": "campaign_create_channel",
                    "data": "63"
                },
                {
                    "name": "has_active_creation",
                    "data": "0"
                },
                {
                    "name": "campaign_opt_status",
                    "data": "1"
                },
                {
                    "name": "campaign_budget_mode",
                    "data": "0"
                },
                {
                    "name": "cost_per_onsite_roi2_shopping_sku",
                    "data": "8.53"
                },
                {
                    "name": "campaign_eligible_status",
                    "data": "0"
                },
                {
                    "name": "promotion_days_roas_bid_multiplier",
                    "data": "10"
                },
                {
                    "name": "gmv_max_shop_id",
                    "data": "7494961084692727148"
                },
                {
                    "name": "campaign_id",
                    "data": "1878768422419858"
                },
                {
                    "name": "pricing_value",
                    "data": "9"
                },
                {
                    "name": "campaign_additional_budget",
                    "data": "0"
                },
                {
                    "name": "campaign_name",
                    "data": "商品 GMV Max_总收入_Ridge Little Shop_20261011230801"
                },
                {
                    "name": "onsite_roi2_shopping_sku",
                    "data": "1"
                },
                {
                    "name": "shop_roi2_qcpx_spillover_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "in_promotion_days",
                    "data": "0"
                },
                {
                    "name": "basic_cost",
                    "data": "8.53"
                },
                {
                    "name": "template_ad_roas_bid",
                    "data": "2.80"
                },
                {
                    "name": "campaign_eligible_roi",
                    "data": "0"
                },
                {
                    "name": "campaign_budget_utilization_status",
                    "data": "0"
                },
                {
                    "name": "compensation_info",
                    "data": "{\"query_date\":\"20261014\",\"compensation_status\":3,\"invalid_reasons\":[5],\"end_reasons\":[],\"is_success\":true,\"compensation_amount\":\"\",\"currency\":\"\",\"url\":\"\",\"product_id\":\"\",\"is_within_learning_phase\":false,\"compensate_category\":0}"
                },
                {
                    "name": "promotion_days_schedules",
                    "data": "[{\"start_time\":\"2026-01-24\",\"end_time\":\"2026-02-02\"},{\"start_time\":\"2026-03-01\",\"end_time\":\"2026-03-03\"},{\"start_time\":\"2026-03-24\",\"end_time\":\"2026-03-26\"},{\"start_time\":\"2026-04-01\",\"end_time\":\"2026-04-04\"},{\"start_time\":\"2026-04-24\",\"end_time\":\"2026-04-25\"},{\"start_time\":\"2026-05-03\",\"end_time\":\"2026-05-07\"},{\"start_time\":\"2026-05-24\",\"end_time\":\"2026-05-25\"},{\"start_time\":\"2026-05-30\",\"end_time\":\"2026-06-06\"},{\"start_time\":\"2026-06-24\",\"end_time\":\"2026-06-25\"},{\"start_time\":\"2026-07-04\",\"end_time\":\"2026-07-07\"},{\"start_time\":\"2026-07-24\",\"end_time\":\"2026-07-25\"},{\"start_time\":\"2026-08-04\",\"end_time\":\"2026-08-08\"},{\"start_time\":\"2026-08-24\",\"end_time\":\"2026-08-25\"},{\"start_time\":\"2026-09-03\",\"end_time\":\"2026-09-10\"},{\"start_time\":\"2026-09-24\",\"end_time\":\"2026-09-25\"},{\"start_time\":\"2026-10-07\",\"end_time\":\"2026-10-10\"},{\"start_time\":\"2026-10-24\",\"end_time\":\"2026-10-25\"}]"
                }
            ],
            [
                {
                    "name": "template_ad_schedule_type",
                    "data": "持续投放"
                },
                {
                    "name": "promotion_days_roas_bid_multiplier",
                    "data": "10"
                },
                {
                    "name": "gmax_advanced_mode",
                    "data": "1"
                },
                {
                    "name": "campaign_opt_status",
                    "data": "1"
                },
                {
                    "name": "shop_roi2_qcpx_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "has_active_creation",
                    "data": "0"
                },
                {
                    "name": "basic_cost",
                    "data": "7.54"
                },
                {
                    "name": "qcpx_coupon_cost",
                    "data": "0.00"
                },
                {
                    "name": "campaign_target_roi_budget_mode",
                    "data": "0"
                },
                {
                    "name": "current_optimization_modes",
                    "data": "[1]"
                },
                {
                    "name": "onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "pricing_value",
                    "data": "9"
                },
                {
                    "name": "campaign_create_channel",
                    "data": "63"
                },
                {
                    "name": "cost_per_onsite_roi2_shopping_sku",
                    "data": "0.00"
                },
                {
                    "name": "basic_onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "campaign_id",
                    "data": "1878768515159265"
                },
                {
                    "name": "campaign_status",
                    "data": "roi2_mutex_exclusive_product"
                },
                {
                    "name": "gmv_max_bid_type",
                    "data": "1"
                },
                {
                    "name": "campaign_budget_mode",
                    "data": "0"
                },
                {
                    "name": "billed_cost",
                    "data": "7.54"
                },
                {
                    "name": "cost",
                    "data": "7.54"
                },
                {
                    "name": "promotion_days_budget_multiplier",
                    "data": "50"
                },
                {
                    "name": "onsite_roi2_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "auto_increase_budget_effective_budget",
                    "data": "200.00"
                },
                {
                    "name": "creative_nobid_cost",
                    "data": "0.00"
                },
                {
                    "name": "campaign_primary_status",
                    "data": "disable"
                },
                {
                    "name": "campaign_eligible_reject_reason",
                    "data": "0"
                },
                {
                    "name": "campaign_eligible_roi",
                    "data": "0"
                },
                {
                    "name": "campaign_budget_utilization_yesterday_threshold",
                    "data": "0"
                },
                {
                    "name": "onsite_roi2_shopping_sku",
                    "data": "0"
                },
                {
                    "name": "in_promotion_days",
                    "data": "0"
                },
                {
                    "name": "campaign_budget",
                    "data": "200.00"
                },
                {
                    "name": "template_ad_end_time",
                    "data": "2036-10-08 23:06:16"
                },
                {
                    "name": "campaign_target_roi_budget",
                    "data": "200.00"
                },
                {
                    "name": "campaign_additional_budget",
                    "data": "0"
                },
                {
                    "name": "campaign_eligible_status",
                    "data": "0"
                },
                {
                    "name": "template_ad_start_time",
                    "data": "2026-10-11 23:06:16"
                },
                {
                    "name": "shop_roi2_qcpx_spillover_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "campaign_name",
                    "data": "商品 GMV Max_总收入_Ridge Little Shop_20261011230616"
                },
                {
                    "name": "gmv_max_shop_id",
                    "data": "7494961084692727148"
                },
                {
                    "name": "campaign_budget_utilization_today_threshold",
                    "data": "0"
                },
                {
                    "name": "promotion_days_mode",
                    "data": "1"
                },
                {
                    "name": "template_ad_roas_bid",
                    "data": "2.80"
                },
                {
                    "name": "all_boost_cost",
                    "data": "0.00"
                },
                {
                    "name": "qcpx_setting_is_enabled",
                    "data": "false"
                },
                {
                    "name": "campaign_budget_utilization_status",
                    "data": "0"
                },
                {
                    "name": "compensation_info",
                    "data": "{\"query_date\":\"20261014\",\"compensation_status\":2,\"invalid_reasons\":[],\"end_reasons\":[],\"is_success\":true,\"compensation_amount\":\"\",\"currency\":\"\",\"url\":\"\",\"product_id\":\"\",\"is_within_learning_phase\":false,\"compensate_category\":0}"
                },
                {
                    "name": "promotion_days_schedules",
                    "data": "[{\"start_time\":\"2026-01-24\",\"end_time\":\"2026-02-02\"},{\"start_time\":\"2026-03-01\",\"end_time\":\"2026-03-03\"},{\"start_time\":\"2026-03-24\",\"end_time\":\"2026-03-26\"},{\"start_time\":\"2026-04-01\",\"end_time\":\"2026-04-04\"},{\"start_time\":\"2026-04-24\",\"end_time\":\"2026-04-25\"},{\"start_time\":\"2026-05-03\",\"end_time\":\"2026-05-07\"},{\"start_time\":\"2026-05-24\",\"end_time\":\"2026-05-25\"},{\"start_time\":\"2026-05-30\",\"end_time\":\"2026-06-06\"},{\"start_time\":\"2026-06-24\",\"end_time\":\"2026-06-25\"},{\"start_time\":\"2026-07-04\",\"end_time\":\"2026-07-07\"},{\"start_time\":\"2026-07-24\",\"end_time\":\"2026-07-25\"},{\"start_time\":\"2026-08-04\",\"end_time\":\"2026-08-08\"},{\"start_time\":\"2026-08-24\",\"end_time\":\"2026-08-25\"},{\"start_time\":\"2026-09-03\",\"end_time\":\"2026-09-10\"},{\"start_time\":\"2026-09-24\",\"end_time\":\"2026-09-25\"},{\"start_time\":\"2026-10-07\",\"end_time\":\"2026-10-10\"},{\"start_time\":\"2026-10-24\",\"end_time\":\"2026-10-25\"}]"
                }
            ],
            [
                {
                    "name": "campaign_eligible_reject_reason",
                    "data": "0"
                },
                {
                    "name": "template_ad_end_time",
                    "data": "2036-10-08 17:58:35"
                },
                {
                    "name": "basic_onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "campaign_target_roi_budget_mode",
                    "data": "0"
                },
                {
                    "name": "basic_cost",
                    "data": "6.67"
                },
                {
                    "name": "qcpx_coupon_cost",
                    "data": "0.00"
                },
                {
                    "name": "template_ad_start_time",
                    "data": "2026-10-11 17:58:35"
                },
                {
                    "name": "campaign_budget",
                    "data": "200.00"
                },
                {
                    "name": "in_promotion_days",
                    "data": "0"
                },
                {
                    "name": "shop_roi2_qcpx_spillover_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "promotion_days_mode",
                    "data": "1"
                },
                {
                    "name": "campaign_budget_utilization_today_threshold",
                    "data": "0"
                },
                {
                    "name": "shop_roi2_qcpx_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "onsite_roi2_shopping_sku",
                    "data": "0"
                },
                {
                    "name": "gmv_max_shop_id",
                    "data": "7494961084692727148"
                },
                {
                    "name": "campaign_primary_status",
                    "data": "disable"
                },
                {
                    "name": "cost",
                    "data": "6.67"
                },
                {
                    "name": "campaign_create_channel",
                    "data": "63"
                },
                {
                    "name": "campaign_id",
                    "data": "1878749528158913"
                },
                {
                    "name": "campaign_budget_utilization_yesterday_threshold",
                    "data": "0"
                },
                {
                    "name": "campaign_status",
                    "data": "roi2_mutex_exclusive_product"
                },
                {
                    "name": "billed_cost",
                    "data": "6.36"
                },
                {
                    "name": "gmax_advanced_mode",
                    "data": "1"
                },
                {
                    "name": "promotion_days_roas_bid_multiplier",
                    "data": "10"
                },
                {
                    "name": "template_ad_schedule_type",
                    "data": "持续投放"
                },
                {
                    "name": "campaign_target_roi_budget",
                    "data": "200.00"
                },
                {
                    "name": "has_active_creation",
                    "data": "0"
                },
                {
                    "name": "campaign_additional_budget",
                    "data": "0"
                },
                {
                    "name": "template_ad_roas_bid",
                    "data": "2.80"
                },
                {
                    "name": "onsite_roi2_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "pricing_value",
                    "data": "9"
                },
                {
                    "name": "cost_per_onsite_roi2_shopping_sku",
                    "data": "0.00"
                },
                {
                    "name": "campaign_eligible_roi",
                    "data": "0"
                },
                {
                    "name": "campaign_budget_mode",
                    "data": "0"
                },
                {
                    "name": "campaign_eligible_status",
                    "data": "0"
                },
                {
                    "name": "current_optimization_modes",
                    "data": "[1]"
                },
                {
                    "name": "campaign_name",
                    "data": "商品 GMV Max_总收入_Ridge Little Shop_20261011175835"
                },
                {
                    "name": "onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "all_boost_cost",
                    "data": "0.00"
                },
                {
                    "name": "promotion_days_budget_multiplier",
                    "data": "50"
                },
                {
                    "name": "campaign_opt_status",
                    "data": "1"
                },
                {
                    "name": "qcpx_setting_is_enabled",
                    "data": "false"
                },
                {
                    "name": "gmv_max_bid_type",
                    "data": "1"
                },
                {
                    "name": "creative_nobid_cost",
                    "data": "0.00"
                },
                {
                    "name": "auto_increase_budget_effective_budget",
                    "data": "200.00"
                },
                {
                    "name": "campaign_budget_utilization_status",
                    "data": "0"
                },
                {
                    "name": "compensation_info",
                    "data": "{\"query_date\":\"20261014\",\"compensation_status\":3,\"invalid_reasons\":[5],\"end_reasons\":[],\"is_success\":true,\"compensation_amount\":\"\",\"currency\":\"\",\"url\":\"\",\"product_id\":\"\",\"is_within_learning_phase\":false,\"compensate_category\":0}"
                },
                {
                    "name": "promotion_days_schedules",
                    "data": "[{\"start_time\":\"2026-01-24\",\"end_time\":\"2026-02-02\"},{\"start_time\":\"2026-03-01\",\"end_time\":\"2026-03-03\"},{\"start_time\":\"2026-03-24\",\"end_time\":\"2026-03-26\"},{\"start_time\":\"2026-04-01\",\"end_time\":\"2026-04-04\"},{\"start_time\":\"2026-04-24\",\"end_time\":\"2026-04-25\"},{\"start_time\":\"2026-05-03\",\"end_time\":\"2026-05-07\"},{\"start_time\":\"2026-05-24\",\"end_time\":\"2026-05-25\"},{\"start_time\":\"2026-05-30\",\"end_time\":\"2026-06-06\"},{\"start_time\":\"2026-06-24\",\"end_time\":\"2026-06-25\"},{\"start_time\":\"2026-07-04\",\"end_time\":\"2026-07-07\"},{\"start_time\":\"2026-07-24\",\"end_time\":\"2026-07-25\"},{\"start_time\":\"2026-08-04\",\"end_time\":\"2026-08-08\"},{\"start_time\":\"2026-08-24\",\"end_time\":\"2026-08-25\"},{\"start_time\":\"2026-09-03\",\"end_time\":\"2026-09-10\"},{\"start_time\":\"2026-09-24\",\"end_time\":\"2026-09-25\"},{\"start_time\":\"2026-10-07\",\"end_time\":\"2026-10-10\"},{\"start_time\":\"2026-10-24\",\"end_time\":\"2026-10-25\"}]"
                }
            ],
            [
                {
                    "name": "promotion_days_budget_multiplier",
                    "data": "50"
                },
                {
                    "name": "qcpx_setting_is_enabled",
                    "data": "false"
                },
                {
                    "name": "basic_cost",
                    "data": "4.66"
                },
                {
                    "name": "onsite_roi2_shopping_sku",
                    "data": "0"
                },
                {
                    "name": "template_ad_end_time",
                    "data": "2036-10-10 22:20:32"
                },
                {
                    "name": "campaign_additional_budget",
                    "data": "0"
                },
                {
                    "name": "template_ad_start_time",
                    "data": "2026-10-13 22:20:32"
                },
                {
                    "name": "onsite_roi2_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "campaign_budget_utilization_yesterday_threshold",
                    "data": "0"
                },
                {
                    "name": "promotion_days_roas_bid_multiplier",
                    "data": "10"
                },
                {
                    "name": "in_promotion_days",
                    "data": "0"
                },
                {
                    "name": "current_optimization_modes",
                    "data": "[1]"
                },
                {
                    "name": "template_ad_roas_bid",
                    "data": "3.20"
                },
                {
                    "name": "onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "pricing_value",
                    "data": "9"
                },
                {
                    "name": "campaign_create_channel",
                    "data": "63"
                },
                {
                    "name": "creative_nobid_cost",
                    "data": "0.00"
                },
                {
                    "name": "campaign_budget_utilization_today_threshold",
                    "data": "0"
                },
                {
                    "name": "gmax_advanced_mode",
                    "data": "1"
                },
                {
                    "name": "campaign_budget_mode",
                    "data": "0"
                },
                {
                    "name": "campaign_status",
                    "data": "roi2_mutex_exclusive_product"
                },
                {
                    "name": "shop_roi2_qcpx_spillover_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "campaign_name",
                    "data": "商品 GMV Max_总收入_Ridge Little Shop_20261013222032"
                },
                {
                    "name": "qcpx_coupon_cost",
                    "data": "0.00"
                },
                {
                    "name": "all_boost_cost",
                    "data": "0.00"
                },
                {
                    "name": "campaign_eligible_roi",
                    "data": "0"
                },
                {
                    "name": "campaign_opt_status",
                    "data": "1"
                },
                {
                    "name": "campaign_id",
                    "data": "1878948394267281"
                },
                {
                    "name": "shop_roi2_qcpx_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "has_active_creation",
                    "data": "0"
                },
                {
                    "name": "template_ad_schedule_type",
                    "data": "持续投放"
                },
                {
                    "name": "promotion_days_mode",
                    "data": "1"
                },
                {
                    "name": "auto_increase_budget_effective_budget",
                    "data": "200.00"
                },
                {
                    "name": "gmv_max_bid_type",
                    "data": "1"
                },
                {
                    "name": "campaign_budget",
                    "data": "200.00"
                },
                {
                    "name": "basic_onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "campaign_eligible_status",
                    "data": "0"
                },
                {
                    "name": "campaign_eligible_reject_reason",
                    "data": "0"
                },
                {
                    "name": "campaign_target_roi_budget",
                    "data": "200.00"
                },
                {
                    "name": "cost",
                    "data": "4.66"
                },
                {
                    "name": "campaign_primary_status",
                    "data": "disable"
                },
                {
                    "name": "gmv_max_shop_id",
                    "data": "7494961084692727148"
                },
                {
                    "name": "cost_per_onsite_roi2_shopping_sku",
                    "data": "0.00"
                },
                {
                    "name": "campaign_target_roi_budget_mode",
                    "data": "0"
                },
                {
                    "name": "billed_cost",
                    "data": "4.22"
                },
                {
                    "name": "campaign_budget_utilization_status",
                    "data": "0"
                },
                {
                    "name": "compensation_info",
                    "data": "{\"query_date\":\"20261014\",\"compensation_status\":3,\"invalid_reasons\":[5],\"end_reasons\":[],\"is_success\":true,\"compensation_amount\":\"\",\"currency\":\"\",\"url\":\"\",\"product_id\":\"\",\"is_within_learning_phase\":false,\"compensate_category\":0}"
                },
                {
                    "name": "promotion_days_schedules",
                    "data": "[{\"start_time\":\"2026-01-24\",\"end_time\":\"2026-02-02\"},{\"start_time\":\"2026-03-01\",\"end_time\":\"2026-03-03\"},{\"start_time\":\"2026-03-24\",\"end_time\":\"2026-03-26\"},{\"start_time\":\"2026-04-01\",\"end_time\":\"2026-04-04\"},{\"start_time\":\"2026-04-24\",\"end_time\":\"2026-04-25\"},{\"start_time\":\"2026-05-03\",\"end_time\":\"2026-05-07\"},{\"start_time\":\"2026-05-24\",\"end_time\":\"2026-05-25\"},{\"start_time\":\"2026-05-30\",\"end_time\":\"2026-06-06\"},{\"start_time\":\"2026-06-24\",\"end_time\":\"2026-06-25\"},{\"start_time\":\"2026-07-04\",\"end_time\":\"2026-07-07\"},{\"start_time\":\"2026-07-24\",\"end_time\":\"2026-07-25\"},{\"start_time\":\"2026-08-04\",\"end_time\":\"2026-08-08\"},{\"start_time\":\"2026-08-24\",\"end_time\":\"2026-08-25\"},{\"start_time\":\"2026-09-03\",\"end_time\":\"2026-09-10\"},{\"start_time\":\"2026-09-24\",\"end_time\":\"2026-09-25\"},{\"start_time\":\"2026-10-07\",\"end_time\":\"2026-10-10\"},{\"start_time\":\"2026-10-24\",\"end_time\":\"2026-10-25\"}]"
                }
            ],
            [
                {
                    "name": "campaign_target_roi_budget_mode",
                    "data": "0"
                },
                {
                    "name": "all_boost_cost",
                    "data": "0.00"
                },
                {
                    "name": "campaign_status",
                    "data": "roi2_mutex_exclusive_product"
                },
                {
                    "name": "gmv_max_bid_type",
                    "data": "1"
                },
                {
                    "name": "campaign_eligible_reject_reason",
                    "data": "0"
                },
                {
                    "name": "onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "template_ad_schedule_type",
                    "data": "持续投放"
                },
                {
                    "name": "cost",
                    "data": "4.17"
                },
                {
                    "name": "campaign_budget",
                    "data": "200.00"
                },
                {
                    "name": "template_ad_start_time",
                    "data": "2026-10-12 23:09:09"
                },
                {
                    "name": "billed_cost",
                    "data": "2.75"
                },
                {
                    "name": "pricing_value",
                    "data": "9"
                },
                {
                    "name": "campaign_create_channel",
                    "data": "63"
                },
                {
                    "name": "current_optimization_modes",
                    "data": "[1]"
                },
                {
                    "name": "promotion_days_roas_bid_multiplier",
                    "data": "10"
                },
                {
                    "name": "campaign_id",
                    "data": "1878859492794450"
                },
                {
                    "name": "qcpx_coupon_cost",
                    "data": "0.00"
                },
                {
                    "name": "in_promotion_days",
                    "data": "0"
                },
                {
                    "name": "gmv_max_shop_id",
                    "data": "7494961084692727148"
                },
                {
                    "name": "campaign_primary_status",
                    "data": "disable"
                },
                {
                    "name": "campaign_target_roi_budget",
                    "data": "200.00"
                },
                {
                    "name": "has_active_creation",
                    "data": "0"
                },
                {
                    "name": "basic_cost",
                    "data": "4.17"
                },
                {
                    "name": "campaign_eligible_roi",
                    "data": "0"
                },
                {
                    "name": "shop_roi2_qcpx_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "onsite_roi2_shopping_sku",
                    "data": "0"
                },
                {
                    "name": "promotion_days_budget_multiplier",
                    "data": "50"
                },
                {
                    "name": "shop_roi2_qcpx_spillover_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "campaign_budget_utilization_yesterday_threshold",
                    "data": "0"
                },
                {
                    "name": "campaign_opt_status",
                    "data": "1"
                },
                {
                    "name": "creative_nobid_cost",
                    "data": "0.00"
                },
                {
                    "name": "campaign_budget_utilization_today_threshold",
                    "data": "0"
                },
                {
                    "name": "campaign_budget_mode",
                    "data": "0"
                },
                {
                    "name": "basic_onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "promotion_days_mode",
                    "data": "1"
                },
                {
                    "name": "auto_increase_budget_effective_budget",
                    "data": "200.00"
                },
                {
                    "name": "cost_per_onsite_roi2_shopping_sku",
                    "data": "0.00"
                },
                {
                    "name": "campaign_eligible_status",
                    "data": "0"
                },
                {
                    "name": "gmax_advanced_mode",
                    "data": "1"
                },
                {
                    "name": "template_ad_roas_bid",
                    "data": "4.50"
                },
                {
                    "name": "onsite_roi2_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "qcpx_setting_is_enabled",
                    "data": "false"
                },
                {
                    "name": "template_ad_end_time",
                    "data": "2036-10-09 23:09:09"
                },
                {
                    "name": "campaign_additional_budget",
                    "data": "0"
                },
                {
                    "name": "campaign_name",
                    "data": "商品 GMV Max_总收入_Ridge Little Shop_20261012230909"
                },
                {
                    "name": "campaign_budget_utilization_status",
                    "data": "0"
                },
                {
                    "name": "compensation_info",
                    "data": "{\"query_date\":\"20261014\",\"compensation_status\":3,\"invalid_reasons\":[5],\"end_reasons\":[],\"is_success\":true,\"compensation_amount\":\"\",\"currency\":\"\",\"url\":\"\",\"product_id\":\"\",\"is_within_learning_phase\":false,\"compensate_category\":0}"
                },
                {
                    "name": "promotion_days_schedules",
                    "data": "[{\"start_time\":\"2026-01-24\",\"end_time\":\"2026-02-02\"},{\"start_time\":\"2026-03-01\",\"end_time\":\"2026-03-03\"},{\"start_time\":\"2026-03-24\",\"end_time\":\"2026-03-26\"},{\"start_time\":\"2026-04-01\",\"end_time\":\"2026-04-04\"},{\"start_time\":\"2026-04-24\",\"end_time\":\"2026-04-25\"},{\"start_time\":\"2026-05-03\",\"end_time\":\"2026-05-07\"},{\"start_time\":\"2026-05-24\",\"end_time\":\"2026-05-25\"},{\"start_time\":\"2026-05-30\",\"end_time\":\"2026-06-06\"},{\"start_time\":\"2026-06-24\",\"end_time\":\"2026-06-25\"},{\"start_time\":\"2026-07-04\",\"end_time\":\"2026-07-07\"},{\"start_time\":\"2026-07-24\",\"end_time\":\"2026-07-25\"},{\"start_time\":\"2026-08-04\",\"end_time\":\"2026-08-08\"},{\"start_time\":\"2026-08-24\",\"end_time\":\"2026-08-25\"},{\"start_time\":\"2026-09-03\",\"end_time\":\"2026-09-10\"},{\"start_time\":\"2026-09-24\",\"end_time\":\"2026-09-25\"},{\"start_time\":\"2026-10-07\",\"end_time\":\"2026-10-10\"},{\"start_time\":\"2026-10-24\",\"end_time\":\"2026-10-25\"}]"
                }
            ],
            [
                {
                    "name": "campaign_target_roi_budget_mode",
                    "data": "0"
                },
                {
                    "name": "template_ad_roas_bid",
                    "data": "2.80"
                },
                {
                    "name": "promotion_days_roas_bid_multiplier",
                    "data": "10"
                },
                {
                    "name": "in_promotion_days",
                    "data": "0"
                },
                {
                    "name": "shop_roi2_qcpx_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "all_boost_cost",
                    "data": "0.00"
                },
                {
                    "name": "campaign_status",
                    "data": "roi2_mutex_exclusive_product"
                },
                {
                    "name": "cost_per_onsite_roi2_shopping_sku",
                    "data": "0.00"
                },
                {
                    "name": "billed_cost",
                    "data": "2.79"
                },
                {
                    "name": "campaign_primary_status",
                    "data": "disable"
                },
                {
                    "name": "qcpx_setting_is_enabled",
                    "data": "false"
                },
                {
                    "name": "qcpx_coupon_cost",
                    "data": "0.00"
                },
                {
                    "name": "auto_increase_budget_effective_budget",
                    "data": "200.00"
                },
                {
                    "name": "promotion_days_budget_multiplier",
                    "data": "50"
                },
                {
                    "name": "promotion_days_mode",
                    "data": "1"
                },
                {
                    "name": "campaign_eligible_roi",
                    "data": "0"
                },
                {
                    "name": "current_optimization_modes",
                    "data": "[1]"
                },
                {
                    "name": "template_ad_start_time",
                    "data": "2026-10-11 23:05:38"
                },
                {
                    "name": "has_active_creation",
                    "data": "0"
                },
                {
                    "name": "campaign_budget_utilization_yesterday_threshold",
                    "data": "0"
                },
                {
                    "name": "creative_nobid_cost",
                    "data": "0.00"
                },
                {
                    "name": "shop_roi2_qcpx_spillover_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "campaign_id",
                    "data": "1878768337771185"
                },
                {
                    "name": "template_ad_end_time",
                    "data": "2036-10-08 23:05:38"
                },
                {
                    "name": "onsite_roi2_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "cost",
                    "data": "3.73"
                },
                {
                    "name": "gmv_max_shop_id",
                    "data": "7494961084692727148"
                },
                {
                    "name": "campaign_opt_status",
                    "data": "1"
                },
                {
                    "name": "campaign_budget",
                    "data": "200.00"
                },
                {
                    "name": "gmv_max_bid_type",
                    "data": "1"
                },
                {
                    "name": "basic_cost",
                    "data": "3.73"
                },
                {
                    "name": "campaign_eligible_status",
                    "data": "0"
                },
                {
                    "name": "campaign_additional_budget",
                    "data": "0"
                },
                {
                    "name": "gmax_advanced_mode",
                    "data": "1"
                },
                {
                    "name": "campaign_create_channel",
                    "data": "63"
                },
                {
                    "name": "pricing_value",
                    "data": "9"
                },
                {
                    "name": "campaign_name",
                    "data": "商品 GMV Max_总收入_Ridge Little Shop_20261011230538"
                },
                {
                    "name": "campaign_budget_utilization_today_threshold",
                    "data": "0"
                },
                {
                    "name": "basic_onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "campaign_target_roi_budget",
                    "data": "200.00"
                },
                {
                    "name": "template_ad_schedule_type",
                    "data": "持续投放"
                },
                {
                    "name": "campaign_eligible_reject_reason",
                    "data": "0"
                },
                {
                    "name": "campaign_budget_mode",
                    "data": "0"
                },
                {
                    "name": "onsite_roi2_shopping_sku",
                    "data": "0"
                },
                {
                    "name": "campaign_budget_utilization_status",
                    "data": "0"
                },
                {
                    "name": "compensation_info",
                    "data": "{\"query_date\":\"20261014\",\"compensation_status\":3,\"invalid_reasons\":[5],\"end_reasons\":[],\"is_success\":true,\"compensation_amount\":\"\",\"currency\":\"\",\"url\":\"\",\"product_id\":\"\",\"is_within_learning_phase\":false,\"compensate_category\":0}"
                },
                {
                    "name": "promotion_days_schedules",
                    "data": "[{\"start_time\":\"2026-01-24\",\"end_time\":\"2026-02-02\"},{\"start_time\":\"2026-03-01\",\"end_time\":\"2026-03-03\"},{\"start_time\":\"2026-03-24\",\"end_time\":\"2026-03-26\"},{\"start_time\":\"2026-04-01\",\"end_time\":\"2026-04-04\"},{\"start_time\":\"2026-04-24\",\"end_time\":\"2026-04-25\"},{\"start_time\":\"2026-05-03\",\"end_time\":\"2026-05-07\"},{\"start_time\":\"2026-05-24\",\"end_time\":\"2026-05-25\"},{\"start_time\":\"2026-05-30\",\"end_time\":\"2026-06-06\"},{\"start_time\":\"2026-06-24\",\"end_time\":\"2026-06-25\"},{\"start_time\":\"2026-07-04\",\"end_time\":\"2026-07-07\"},{\"start_time\":\"2026-07-24\",\"end_time\":\"2026-07-25\"},{\"start_time\":\"2026-08-04\",\"end_time\":\"2026-08-08\"},{\"start_time\":\"2026-08-24\",\"end_time\":\"2026-08-25\"},{\"start_time\":\"2026-09-03\",\"end_time\":\"2026-09-10\"},{\"start_time\":\"2026-09-24\",\"end_time\":\"2026-09-25\"},{\"start_time\":\"2026-10-07\",\"end_time\":\"2026-10-10\"},{\"start_time\":\"2026-10-24\",\"end_time\":\"2026-10-25\"}]"
                }
            ],
            [
                {
                    "name": "gmv_max_shop_id",
                    "data": "7494961084692727148"
                },
                {
                    "name": "onsite_roi2_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "shop_roi2_qcpx_spillover_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "current_optimization_modes",
                    "data": "[1]"
                },
                {
                    "name": "campaign_create_channel",
                    "data": "63"
                },
                {
                    "name": "gmv_max_bid_type",
                    "data": "1"
                },
                {
                    "name": "onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "template_ad_schedule_type",
                    "data": "持续投放"
                },
                {
                    "name": "campaign_budget_utilization_today_threshold",
                    "data": "0"
                },
                {
                    "name": "cost_per_onsite_roi2_shopping_sku",
                    "data": "0.00"
                },
                {
                    "name": "onsite_roi2_shopping_sku",
                    "data": "0"
                },
                {
                    "name": "shop_roi2_qcpx_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "pricing_value",
                    "data": "9"
                },
                {
                    "name": "campaign_name",
                    "data": "商品 GMV Max_总收入_Ridge Little Shop_20261013222513"
                },
                {
                    "name": "cost",
                    "data": "3.14"
                },
                {
                    "name": "campaign_eligible_roi",
                    "data": "0"
                },
                {
                    "name": "campaign_id",
                    "data": "1878949197637009"
                },
                {
                    "name": "template_ad_roas_bid",
                    "data": "3.20"
                },
                {
                    "name": "has_active_creation",
                    "data": "0"
                },
                {
                    "name": "campaign_budget_mode",
                    "data": "0"
                },
                {
                    "name": "campaign_opt_status",
                    "data": "1"
                },
                {
                    "name": "promotion_days_roas_bid_multiplier",
                    "data": "10"
                },
                {
                    "name": "campaign_eligible_status",
                    "data": "0"
                },
                {
                    "name": "campaign_target_roi_budget_mode",
                    "data": "0"
                },
                {
                    "name": "promotion_days_budget_multiplier",
                    "data": "50"
                },
                {
                    "name": "campaign_additional_budget",
                    "data": "0"
                },
                {
                    "name": "qcpx_coupon_cost",
                    "data": "0.00"
                },
                {
                    "name": "campaign_budget",
                    "data": "200.00"
                },
                {
                    "name": "basic_onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "all_boost_cost",
                    "data": "0.00"
                },
                {
                    "name": "gmax_advanced_mode",
                    "data": "1"
                },
                {
                    "name": "creative_nobid_cost",
                    "data": "0.00"
                },
                {
                    "name": "in_promotion_days",
                    "data": "0"
                },
                {
                    "name": "promotion_days_mode",
                    "data": "1"
                },
                {
                    "name": "qcpx_setting_is_enabled",
                    "data": "false"
                },
                {
                    "name": "campaign_eligible_reject_reason",
                    "data": "0"
                },
                {
                    "name": "campaign_target_roi_budget",
                    "data": "200.00"
                },
                {
                    "name": "auto_increase_budget_effective_budget",
                    "data": "200.00"
                },
                {
                    "name": "template_ad_start_time",
                    "data": "2026-10-13 22:25:13"
                },
                {
                    "name": "campaign_budget_utilization_yesterday_threshold",
                    "data": "0"
                },
                {
                    "name": "billed_cost",
                    "data": "2.50"
                },
                {
                    "name": "basic_cost",
                    "data": "3.14"
                },
                {
                    "name": "campaign_status",
                    "data": "roi2_mutex_exclusive_product"
                },
                {
                    "name": "template_ad_end_time",
                    "data": "2036-10-10 22:25:13"
                },
                {
                    "name": "campaign_primary_status",
                    "data": "disable"
                },
                {
                    "name": "campaign_budget_utilization_status",
                    "data": "0"
                },
                {
                    "name": "compensation_info",
                    "data": "{\"query_date\":\"20261014\",\"compensation_status\":3,\"invalid_reasons\":[5],\"end_reasons\":[],\"is_success\":true,\"compensation_amount\":\"\",\"currency\":\"\",\"url\":\"\",\"product_id\":\"\",\"is_within_learning_phase\":false,\"compensate_category\":0}"
                },
                {
                    "name": "promotion_days_schedules",
                    "data": "[{\"start_time\":\"2026-01-24\",\"end_time\":\"2026-02-02\"},{\"start_time\":\"2026-03-01\",\"end_time\":\"2026-03-03\"},{\"start_time\":\"2026-03-24\",\"end_time\":\"2026-03-26\"},{\"start_time\":\"2026-04-01\",\"end_time\":\"2026-04-04\"},{\"start_time\":\"2026-04-24\",\"end_time\":\"2026-04-25\"},{\"start_time\":\"2026-05-03\",\"end_time\":\"2026-05-07\"},{\"start_time\":\"2026-05-24\",\"end_time\":\"2026-05-25\"},{\"start_time\":\"2026-05-30\",\"end_time\":\"2026-06-06\"},{\"start_time\":\"2026-06-24\",\"end_time\":\"2026-06-25\"},{\"start_time\":\"2026-07-04\",\"end_time\":\"2026-07-07\"},{\"start_time\":\"2026-07-24\",\"end_time\":\"2026-07-25\"},{\"start_time\":\"2026-08-04\",\"end_time\":\"2026-08-08\"},{\"start_time\":\"2026-08-24\",\"end_time\":\"2026-08-25\"},{\"start_time\":\"2026-09-03\",\"end_time\":\"2026-09-10\"},{\"start_time\":\"2026-09-24\",\"end_time\":\"2026-09-25\"},{\"start_time\":\"2026-10-07\",\"end_time\":\"2026-10-10\"},{\"start_time\":\"2026-10-24\",\"end_time\":\"2026-10-25\"}]"
                }
            ],
            [
                {
                    "name": "promotion_days_roas_bid_multiplier",
                    "data": "10"
                },
                {
                    "name": "campaign_primary_status",
                    "data": "disable"
                },
                {
                    "name": "shop_roi2_qcpx_spillover_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "qcpx_coupon_cost",
                    "data": "0.00"
                },
                {
                    "name": "campaign_eligible_roi",
                    "data": "0"
                },
                {
                    "name": "campaign_name",
                    "data": "商品 GMV Max_总收入_Ridge Little Shop_20261013211533"
                },
                {
                    "name": "current_optimization_modes",
                    "data": "[1]"
                },
                {
                    "name": "promotion_days_mode",
                    "data": "1"
                },
                {
                    "name": "gmax_advanced_mode",
                    "data": "1"
                },
                {
                    "name": "campaign_budget_utilization_yesterday_threshold",
                    "data": "0"
                },
                {
                    "name": "campaign_eligible_reject_reason",
                    "data": "0"
                },
                {
                    "name": "basic_onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "onsite_roi2_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "pricing_value",
                    "data": "9"
                },
                {
                    "name": "template_ad_roas_bid",
                    "data": "3.20"
                },
                {
                    "name": "campaign_budget_mode",
                    "data": "0"
                },
                {
                    "name": "campaign_budget",
                    "data": "200.00"
                },
                {
                    "name": "gmv_max_bid_type",
                    "data": "1"
                },
                {
                    "name": "campaign_opt_status",
                    "data": "1"
                },
                {
                    "name": "template_ad_schedule_type",
                    "data": "持续投放"
                },
                {
                    "name": "cost",
                    "data": "2.40"
                },
                {
                    "name": "basic_cost",
                    "data": "2.40"
                },
                {
                    "name": "in_promotion_days",
                    "data": "0"
                },
                {
                    "name": "shop_roi2_qcpx_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "cost_per_onsite_roi2_shopping_sku",
                    "data": "0.00"
                },
                {
                    "name": "gmv_max_shop_id",
                    "data": "7494961084692727148"
                },
                {
                    "name": "billed_cost",
                    "data": "2.17"
                },
                {
                    "name": "has_active_creation",
                    "data": "0"
                },
                {
                    "name": "onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "qcpx_setting_is_enabled",
                    "data": "false"
                },
                {
                    "name": "campaign_eligible_status",
                    "data": "0"
                },
                {
                    "name": "campaign_target_roi_budget_mode",
                    "data": "0"
                },
                {
                    "name": "campaign_additional_budget",
                    "data": "0"
                },
                {
                    "name": "auto_increase_budget_effective_budget",
                    "data": "200.00"
                },
                {
                    "name": "onsite_roi2_shopping_sku",
                    "data": "0"
                },
                {
                    "name": "campaign_target_roi_budget",
                    "data": "200.00"
                },
                {
                    "name": "template_ad_start_time",
                    "data": "2026-10-13 21:15:33"
                },
                {
                    "name": "template_ad_end_time",
                    "data": "2036-10-10 21:15:33"
                },
                {
                    "name": "campaign_budget_utilization_today_threshold",
                    "data": "0"
                },
                {
                    "name": "all_boost_cost",
                    "data": "0.00"
                },
                {
                    "name": "campaign_id",
                    "data": "1878945517890898"
                },
                {
                    "name": "promotion_days_budget_multiplier",
                    "data": "50"
                },
                {
                    "name": "creative_nobid_cost",
                    "data": "0.00"
                },
                {
                    "name": "campaign_create_channel",
                    "data": "63"
                },
                {
                    "name": "campaign_status",
                    "data": "roi2_mutex_exclusive_product"
                },
                {
                    "name": "campaign_budget_utilization_status",
                    "data": "0"
                },
                {
                    "name": "compensation_info",
                    "data": "{\"query_date\":\"20261014\",\"compensation_status\":3,\"invalid_reasons\":[5],\"end_reasons\":[],\"is_success\":true,\"compensation_amount\":\"\",\"currency\":\"\",\"url\":\"\",\"product_id\":\"\",\"is_within_learning_phase\":false,\"compensate_category\":0}"
                },
                {
                    "name": "promotion_days_schedules",
                    "data": "[{\"start_time\":\"2026-01-24\",\"end_time\":\"2026-02-02\"},{\"start_time\":\"2026-03-01\",\"end_time\":\"2026-03-03\"},{\"start_time\":\"2026-03-24\",\"end_time\":\"2026-03-26\"},{\"start_time\":\"2026-04-01\",\"end_time\":\"2026-04-04\"},{\"start_time\":\"2026-04-24\",\"end_time\":\"2026-04-25\"},{\"start_time\":\"2026-05-03\",\"end_time\":\"2026-05-07\"},{\"start_time\":\"2026-05-24\",\"end_time\":\"2026-05-25\"},{\"start_time\":\"2026-05-30\",\"end_time\":\"2026-06-06\"},{\"start_time\":\"2026-06-24\",\"end_time\":\"2026-06-25\"},{\"start_time\":\"2026-07-04\",\"end_time\":\"2026-07-07\"},{\"start_time\":\"2026-07-24\",\"end_time\":\"2026-07-25\"},{\"start_time\":\"2026-08-04\",\"end_time\":\"2026-08-08\"},{\"start_time\":\"2026-08-24\",\"end_time\":\"2026-08-25\"},{\"start_time\":\"2026-09-03\",\"end_time\":\"2026-09-10\"},{\"start_time\":\"2026-09-24\",\"end_time\":\"2026-09-25\"},{\"start_time\":\"2026-10-07\",\"end_time\":\"2026-10-10\"},{\"start_time\":\"2026-10-24\",\"end_time\":\"2026-10-25\"}]"
                }
            ],
            [
                {
                    "name": "current_optimization_modes",
                    "data": "[1]"
                },
                {
                    "name": "template_ad_roas_bid",
                    "data": "6.00"
                },
                {
                    "name": "onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "campaign_budget_mode",
                    "data": "0"
                },
                {
                    "name": "template_ad_schedule_type",
                    "data": "持续投放"
                },
                {
                    "name": "template_ad_start_time",
                    "data": "2026-10-14 16:22:56"
                },
                {
                    "name": "campaign_primary_status",
                    "data": "delivery_ok"
                },
                {
                    "name": "billed_cost",
                    "data": "0.00"
                },
                {
                    "name": "basic_cost",
                    "data": "0.39"
                },
                {
                    "name": "cost_per_onsite_roi2_shopping_sku",
                    "data": "0.00"
                },
                {
                    "name": "auto_increase_budget_effective_budget",
                    "data": "2000.00"
                },
                {
                    "name": "campaign_name",
                    "data": "商品 GMV Max_总收入_Ridge Little Shop_20261014162256"
                },
                {
                    "name": "onsite_roi2_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "pricing_value",
                    "data": "9"
                },
                {
                    "name": "promotion_days_budget_multiplier",
                    "data": "50"
                },
                {
                    "name": "cost",
                    "data": "0.39"
                },
                {
                    "name": "onsite_roi2_shopping_sku",
                    "data": "0"
                },
                {
                    "name": "gmv_max_shop_id",
                    "data": "7494961084692727148"
                },
                {
                    "name": "campaign_eligible_roi",
                    "data": "0"
                },
                {
                    "name": "shop_roi2_qcpx_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "campaign_id",
                    "data": "1879016462613969"
                },
                {
                    "name": "gmv_max_bid_type",
                    "data": "1"
                },
                {
                    "name": "campaign_opt_status",
                    "data": "0"
                },
                {
                    "name": "basic_onsite_roi2_shopping",
                    "data": "0.00"
                },
                {
                    "name": "gmax_advanced_mode",
                    "data": "1"
                },
                {
                    "name": "campaign_eligible_status",
                    "data": "0"
                },
                {
                    "name": "campaign_eligible_reject_reason",
                    "data": "0"
                },
                {
                    "name": "campaign_target_roi_budget",
                    "data": "2000.00"
                },
                {
                    "name": "campaign_target_roi_budget_mode",
                    "data": "0"
                },
                {
                    "name": "campaign_additional_budget",
                    "data": "0"
                },
                {
                    "name": "promotion_days_roas_bid_multiplier",
                    "data": "10"
                },
                {
                    "name": "has_active_creation",
                    "data": "0"
                },
                {
                    "name": "template_ad_end_time",
                    "data": "2036-10-11 16:22:56"
                },
                {
                    "name": "promotion_days_mode",
                    "data": "1"
                },
                {
                    "name": "campaign_create_channel",
                    "data": "63"
                },
                {
                    "name": "campaign_budget",
                    "data": "2000.00"
                },
                {
                    "name": "campaign_budget_utilization_yesterday_threshold",
                    "data": "0"
                },
                {
                    "name": "all_boost_cost",
                    "data": "0.00"
                },
                {
                    "name": "qcpx_coupon_cost",
                    "data": "0.00"
                },
                {
                    "name": "creative_nobid_cost",
                    "data": "0.00"
                },
                {
                    "name": "campaign_budget_utilization_today_threshold",
                    "data": "0"
                },
                {
                    "name": "shop_roi2_qcpx_spillover_shopping_value",
                    "data": "0.00"
                },
                {
                    "name": "in_promotion_days",
                    "data": "0"
                },
                {
                    "name": "campaign_status",
                    "data": "campaign_delivery_ok"
                },
                {
                    "name": "qcpx_setting_is_enabled",
                    "data": "false"
                },
                {
                    "name": "campaign_budget_utilization_status",
                    "data": "0"
                },
                {
                    "name": "compensation_info",
                    "data": "{\"query_date\":\"20261014\",\"compensation_status\":2,\"invalid_reasons\":[],\"end_reasons\":[],\"is_success\":true,\"compensation_amount\":\"\",\"currency\":\"\",\"url\":\"\",\"product_id\":\"\",\"is_within_learning_phase\":false,\"compensate_category\":0}"
                },
                {
                    "name": "promotion_days_schedules",
                    "data": "[{\"start_time\":\"2026-01-24\",\"end_time\":\"2026-02-02\"},{\"start_time\":\"2026-03-01\",\"end_time\":\"2026-03-03\"},{\"start_time\":\"2026-03-24\",\"end_time\":\"2026-03-26\"},{\"start_time\":\"2026-04-01\",\"end_time\":\"2026-04-04\"},{\"start_time\":\"2026-04-24\",\"end_time\":\"2026-04-25\"},{\"start_time\":\"2026-05-03\",\"end_time\":\"2026-05-07\"},{\"start_time\":\"2026-05-24\",\"end_time\":\"2026-05-25\"},{\"start_time\":\"2026-05-30\",\"end_time\":\"2026-06-06\"},{\"start_time\":\"2026-06-24\",\"end_time\":\"2026-06-25\"},{\"start_time\":\"2026-07-04\",\"end_time\":\"2026-07-07\"},{\"start_time\":\"2026-07-24\",\"end_time\":\"2026-07-25\"},{\"start_time\":\"2026-08-04\",\"end_time\":\"2026-08-08\"},{\"start_time\":\"2026-08-24\",\"end_time\":\"2026-08-25\"},{\"start_time\":\"2026-09-03\",\"end_time\":\"2026-09-10\"},{\"start_time\":\"2026-09-24\",\"end_time\":\"2026-09-25\"},{\"start_time\":\"2026-10-07\",\"end_time\":\"2026-10-10\"},{\"start_time\":\"2026-10-24\",\"end_time\":\"2026-10-25\"}]"
                }
            ]
        ],
        "statistics": {
            "all_boost_cost": "-",
            "auto_increase_budget_effective_budget": "-",
            "basic_cost": "-",
            "basic_onsite_roi2_shopping": "-",
            "billed_cost": "48.23",
            "campaign_additional_budget": "-",
            "campaign_budget": "-",
            "campaign_budget_mode": "-",
            "campaign_budget_utilization_status": "-",
            "campaign_budget_utilization_today_threshold": "-",
            "campaign_budget_utilization_yesterday_threshold": "-",
            "campaign_create_channel": "-",
            "campaign_eligible_reject_reason": "-",
            "campaign_eligible_roi": "-",
            "campaign_eligible_status": "-",
            "campaign_name": "-",
            "campaign_opt_status": "-",
            "campaign_primary_status": "-",
            "campaign_status": "-",
            "campaign_target_roi_budget": "-",
            "campaign_target_roi_budget_mode": "-",
            "compensation_info": "-",
            "cost": "54.42",
            "cost_per_onsite_roi2_shopping_sku": "27.21",
            "creative_nobid_cost": "-",
            "current_optimization_modes": "-",
            "gmax_advanced_mode": "-",
            "gmv_max_bid_type": "-",
            "gmv_max_shop_id": "-",
            "has_active_creation": "-",
            "in_promotion_days": "-",
            "onsite_roi2_shopping": "0.82",
            "onsite_roi2_shopping_sku": "2",
            "onsite_roi2_shopping_value": "44.45",
            "pricing_value": "-",
            "promotion_days_budget_multiplier": "-",
            "promotion_days_mode": "-",
            "promotion_days_roas_bid_multiplier": "-",
            "promotion_days_schedules": "-",
            "qcpx_coupon_cost": "0.00",
            "qcpx_setting_is_enabled": "-",
            "session_info": "-",
            "shop_roi2_qcpx_shopping_value": "0.00",
            "shop_roi2_qcpx_spillover_shopping_value": "0.00",
            "template_ad_end_time": "-",
            "template_ad_roas_bid": "-",
            "template_ad_schedule_type": "-",
            "template_ad_start_time": "-"
        }
    }
}。你可以根据 参数和response 看下新老接口的差异
```


## 关闭与开启：完整用户原文

```text
ok，帮我继续分析：curl 'https://seller.tiktokshopglobalselling.com/oec_ads/shopping/v1/creation/campaign/update_status?locale=zh&language=zh&oec_seller_id=7494961084692727148&aadvid=7694121351103168530' \
  -H 'Accept: application/json, text/plain, */*' \
  -H 'Accept-Language: zh-HK,zh-TW;q=0.9,zh;q=0.8' \
  -H 'Connection: keep-alive' \
  -H 'Content-Type: application/json; charset=UTF-8' \
  -b '[REDACTED_COOKIE]' \
  -H 'Origin: https://seller.tiktokshopglobalselling.com' \
  -H 'Referer: https://seller.tiktokshopglobalselling.com/ads-creation/dashboard?mpa=1&activated_tab_id=0&type=product&shop_region=VN' \
  -H 'Sec-Fetch-Dest: empty' \
  -H 'Sec-Fetch-Mode: cors' \
  -H 'Sec-Fetch-Site: same-origin' \
  -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36' \
  -H 'X-CSRFToken: [REDACTED_CREDENTIAL]' \
  -H 'sec-ch-ua: "Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "Windows"' \
  --data-raw '{"campaign_list":["1879016462613969"],"operation":2}'  这是关闭广告的接口参数，response ：{"code":0,"msg":"success","data":{"status":"success"}}；这是开启广告的curl：curl 'https://seller.tiktokshopglobalselling.com/oec_ads/shopping/v1/creation/campaign/update_status?locale=zh&language=zh&oec_seller_id=7494961084692727148&aadvid=7694121351103168530' \
  -H 'Accept: application/json, text/plain, */*' \
  -H 'Accept-Language: zh-HK,zh-TW;q=0.9,zh;q=0.8' \
  -H 'Connection: keep-alive' \
  -H 'Content-Type: application/json; charset=UTF-8' \
  -b '[REDACTED_COOKIE]' \
  -H 'Origin: https://seller.tiktokshopglobalselling.com' \
  -H 'Referer: https://seller.tiktokshopglobalselling.com/ads-creation/dashboard?mpa=1&activated_tab_id=0&type=product&shop_region=VN' \
  -H 'Sec-Fetch-Dest: empty' \
  -H 'Sec-Fetch-Mode: cors' \
  -H 'Sec-Fetch-Site: same-origin' \
  -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36' \
  -H 'X-CSRFToken: [REDACTED_CREDENTIAL]' \
  -H 'sec-ch-ua: "Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"' \
  -H 'sec-ch-ua-mobile: ?0' \
  -H 'sec-ch-ua-platform: "Windows"' \
  --data-raw '{"campaign_list":["1879016462613969"],"operation":1}'，response：{"code":0,"msg":"success","data":{"status":"success"}}。帮我看下和现在的逻辑的区别
```
