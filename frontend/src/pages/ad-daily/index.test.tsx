import { readFileSync } from 'node:fs';
import { act, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
const row = { id: 7, seller_id: '900719925474099312345', shop_pk: null, shop_name: null, advertiser_id: 'TEST_ADV', campaign_id: 'TEST_CAMPAIGN', product_id: 'TEST_PRODUCT', product_title: null, endpoint: '/TEST/a?x=1&y=2', day: '2026-09-03', mixed_real_cost: '9999999999999999.9999', onsite_roi2_shopping_sku: 0, onsite_roi2_shopping_value: '0.0000', onsite_mixed_real_roi2_shopping: null, metrics_extra: { clicks: 0 }, created_at: '2026-09-03T00:00:00+00:00', updated_at: '2026-09-03T00:00:00+00:00' };
const payload = { items: [row], total: 1, limit: 50, offset: 0, summary: { row_count: 1, spend: row.mixed_real_cost, attributed_orders: 0, attributed_gmv: '0.0000', weighted_roi: null, currency: 'USD' } };
const options = { sellers: [{ seller_id: row.seller_id, shop_name: null, row_count: 1 }], advertisers: [{ seller_id: row.seller_id, advertiser_id: row.advertiser_id, row_count: 1 }], endpoints: [{ endpoint: row.endpoint, row_count: 1 }], min_day: row.day, max_day: row.day };
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
afterEach(() => { vi.unstubAllGlobals(); document.body.replaceChildren(); });
it('preserves every source decimal digit in the displayed spend instead of binary rounding', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.endsWith('/options') ? options : payload)));
  const python = readFileSync('../tts_erp_v2/api/v2/ad_daily.py', 'utf8');
  const html = python.split('_PAGE_HTML = """')[1].split('"""')[0];
  document.body.replaceChildren(...new DOMParser().parseFromString(html, 'text/html').body.childNodes);
  const script = readFileSync('../tts_erp_v2/static/js/ad-daily.js', 'utf8');
  await act(async () => { window.eval(script); document.dispatchEvent(new Event('DOMContentLoaded')); });
  expect(await screen.findAllByText('9,999,999,999,999,999.9999')).toHaveLength(2);
});
