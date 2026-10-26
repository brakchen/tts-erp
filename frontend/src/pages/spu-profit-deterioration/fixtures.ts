export const testItem = {
  shopPk: 11, spuPk: 22, spuId: 'TEST_900719925474099312345', windowDays: 7, anchorDate: '2026-10-03',
  previousRoi: '-0.50', currentRoi: '0', previousNetProfitCny: '9999999999999999.99', currentNetProfitCny: '-100.00',
  previousSpendCny: null, currentSpendCny: '100.00', previousOrderCount: null, currentOrderCount: 0,
  previousNoOrderAdSpend: null, currentNoOrderAdSpend: true,
  previousRoiUnavailableReason: null, currentRoiUnavailableReason: null,
  warnings: ['TEST：部分事实缺失'], basisCalculatedAt: '2026-10-05T00:00:00+00:00',
  drilldown: { pageUrl: '/v2/pages/spu-roi?shop_pk=11&spu_ids=TEST_900719925474099312345&w_start=2026-09-27&w_end=2026-10-03', period: 'current', startDate: '2026-09-27', endDate: '2026-10-03' },
};
export const testPayload = {
  items: [testItem], total: 101,
  totals: { analysisSpuCount: 101, previousOrderCount: null, currentOrderCount: 5, unavailableReason: 'TEST：上期统计不可用' },
  meta: {
    shopPk: 11, anchorDate: '2026-10-03', latestAvailableDate: '2026-10-03', dataAsOf: '2026-10-03', windowDays: 7,
    periods: { current: { startDate: '2026-09-27', endDate: '2026-10-03' }, previous: { startDate: '2026-09-20', endDate: '2026-09-26' } },
    reportingTimezone: 'Asia/Ho_Chi_Minh', calculatedAt: '2026-10-05T00:00:00+00:00', stale: true,
    activityBypassed: false, scopeDescription: 'TEST：固定近7天活动范围', warnings: ['TEST：不是最终会计利润'], requestId: 'TEST_REQUEST',
    coverage: {
      status: 'limited', basis: 'observed_sources_and_inferred_operational_cutoff',
      sources: { orders: { status: 'observed', runId: 12, startedAt: '2026-10-04T00:00:00Z', finishedAt: '2026-10-04T01:00:00Z', operationalCutoff: '2026-10-03', reportedFailures: 0 } },
      advertising: { endpoint: '/TEST/ad', startDate: '2026-09-20', endDate: '2026-10-03', missingDates: ['2026-09-21'], observedScopes: [{ advertiserId: 'TEST_ADV', campaignId: 'TEST_CAMPAIGN', days: ['2026-10-03'] }] },
      limitations: ['TEST：不证明上游完整'],
    },
  },
};
export const testPayload3 = {
  ...testPayload,
  items: [{ ...testItem, windowDays: 3, drilldown: { ...testItem.drilldown, startDate: '2026-10-01', pageUrl: '/v2/pages/spu-roi?shop_pk=11&spu_ids=TEST_900719925474099312345&w_start=2026-10-01&w_end=2026-10-03' } }],
  meta: { ...testPayload.meta, windowDays: 3, periods: { current: { startDate: '2026-10-01', endDate: '2026-10-03' }, previous: { startDate: '2026-09-28', endDate: '2026-09-30' } } },
};
