/* Standard SPU ROI PageProfile bootstrap.
   The shared lifecycle and render implementation live in
   spu-profitability-page.js; this file owns only standard-page policy. */

(() => {
  window.ttsErpPageProfile = {
    id: "standard-roi",
    pagePath: "/v2/pages/spu-roi",
    preferences: {
      storageKey: "tts-erp:spu-roi:preferences:v1",
    },
    dateRangeControl: {
      enabled: true,
      reportingTimeZone: "Asia/Ho_Chi_Minh",
    },
    defaults: {
      includeAll: false,
      limit: 100,
      sort: "roi_real",
      order: "asc",
    },
    view: {
      summaryIds: [
        "sum-total-orders",
        "sum-spend",
        "sum-orders",
        "sum-sales",
        "sum-refund-count",
        "sum-refund-rate",
        "sum-loss-qty",
        "sum-loss-rate",
        "sum-cancel-count",
        "sum-cancel-rate",
        "sum-net-profit",
        "sum-roi",
        "sum-roi-breakeven",
        "sum-roi-ad-actual",
        "sum-roi-ad",
      ],
      columnIds: [
        "product",
        "spend",
        "ad-actual-roi",
        "ad-breakeven-roi",
        "effective-sales",
        "total-orders",
        "effective-orders",
        "cancel-rate",
        "full-loss-rate",
        "net-profit",
      ],
      drillTabIds: ["pnl", "orders", "settlements", "cases", "ads"],
    },
    extensions: [],
  };
})();
