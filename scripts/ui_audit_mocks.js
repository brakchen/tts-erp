#!/usr/bin/env node
/* 共享 mock：布局巡检与字体巡检用**同一份** ad-daily 载荷。
 *
 * 为什么抽出来（2026-10-04, lane e2e-ui-render）
 * ---------------------------------------------
 * scripts/probe_ui_layout_audit.js 曾自带一份旧契约的 ad-daily mock
 * （stat_date / campaign_name / spend ...），而现行
 * `GET /v2/reporting/ad-daily` 的字段是 day / seller_id / product_title /
 * mixed_real_cost / onsite_* + summary.row_count。结果是巡检截图里明细行
 * 全是「—」、shop/adv 显示 undefined —— 页面在**残缺数据**上被巡检，
 * 字号/列宽回归自然漏检（finding f-6001d47e-db2）。
 *
 * 契约来源：tts_erp_v2/api/v2/ad_daily.py::_item / list_ad_daily 的 summary。
 * 契约变了先改这里，再让两支探针一起用它。tests/api/test_ad_daily.py 有静态锁。
 */
"use strict";

const NOW = "2026-10-02T12:00:00+00:00";

const SELLERS = [
  { seller_id: "749486486860415", shop_name: "North Nook", row_count: 4038 },
  {
    seller_id: "749486486860914",
    shop_name: "QA 店 8（很长的店铺名称测试截断行为）",
    row_count: 4038,
  },
];

/**
 * @param {string} url 请求 URL（可带 query）
 * @returns {string|null} 命中 ad-daily 两个只读端点时返回 JSON 字符串，否则 null
 */
function adDailyMock(url) {
  const path = url.replace(/\?.*$/, "");

  if (path.endsWith("/v2/reporting/ad-daily/options")) {
    return JSON.stringify({
      sellers: SELLERS,
      advertisers: SELLERS.map((seller, i) => ({
        seller_id: seller.seller_id,
        advertiser_id: `76793575728722247${i}6`,
        row_count: seller.row_count,
      })),
      endpoints: [
        { endpoint: "/open_api/ads/manager/report/integrated", row_count: 5000 },
        { endpoint: "/open_api/ads/manager/report/daily", row_count: 3076 },
      ],
      min_day: "2026-07-01",
      max_day: "2026-10-03",
    });
  }

  if (path.endsWith("/v2/reporting/ad-daily")) {
    const items = Array.from({ length: 12 }, (_, i) => ({
      id: 9000 + i,
      seller_id: SELLERS[i % 2].seller_id,
      shop_pk: 7,
      shop_name: SELLERS[i % 2].shop_name,
      advertiser_id: `767935757287222470${i % 2}6`,
      campaign_id: "1877052518779186",
      product_id: `1737532${String(867998766722 + i)}`.slice(0, 19),
      product_title:
        i % 2
          ? "Áo sơ mi nam ngắn tay màu xám, họa tiết nhỏ thanh lịch"
          : "Áo polo nam ngắn tay màu đỏ rượu vang cao cấp",
      endpoint: "/open_api/ads/manager/report/integrated",
      day: `2026-10-0${(i % 3) + 1}`,
      mixed_real_cost: (0.01 + i * 0.03).toFixed(2),
      onsite_roi2_shopping_sku: i % 4,
      onsite_roi2_shopping_value: (i * 12.5).toFixed(2),
      onsite_mixed_real_roi2_shopping: i % 5 === 0 ? null : (i * 0.42).toFixed(2),
      metrics_extra: { clicks: 12 + i, impressions: 900 + i },
      created_at: NOW,
      updated_at: "2026-10-04T01:29:00+00:00",
    }));
    return JSON.stringify({
      items,
      total: 8076,
      limit: 50,
      offset: 0,
      summary: {
        row_count: 8076,
        spend: "6674.41",
        attributed_orders: 881,
        attributed_gmv: "21444.63",
        weighted_roi: "3.213",
        currency: "USD",
      },
    });
  }

  return null;
}

module.exports = { adDailyMock, NOW, SELLERS };
