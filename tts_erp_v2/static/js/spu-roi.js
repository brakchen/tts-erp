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
      defaultRange: "t-1",
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
        "sum-projection-status",
        "sum-projection-basis-orders",
        "sum-projection-full-loss-basis-orders",
        "sum-projection-refund-rate",
        "sum-projection-full-loss-rate",
        "sum-unresolved-orders",
        "sum-delivered-unsettled-orders",
        "sum-projected-future-loss-qty",
        "sum-projected-net-revenue",
        "sum-projected-net-profit",
        "sum-projected-roi",
        "sum-projected-breakeven-roi",
        "sum-projected-ad-roi",
        "sum-projected-ad-breakeven-roi",
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
