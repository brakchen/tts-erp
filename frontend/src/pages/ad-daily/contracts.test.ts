import { expect, it } from 'vitest';
import { decodeList, decodeOptions } from './contracts';
const summary = { row_count: 0, spend: '9999999999999999.9999', attributed_orders: 0, attributed_gmv: '0.0000', weighted_roi: null, currency: 'USD' };
it('keeps financial strings, zero, null and server total without deriving anything', () => {
  expect(decodeList({ items: [], total: 900, limit: 50, offset: 100, summary }).summary).toEqual(summary);
  expect(decodeOptions({ sellers: [{ seller_id: '900719925474099312345', shop_name: null, row_count: 1 }], advertisers: [], endpoints: [], min_day: null, max_day: null }).sellers[0].seller_id).toBe('900719925474099312345');
});
it.each([null, {}, { items: null }, { items: [], total: -1, limit: 50, offset: 0, summary }, { items: [], total: 0, limit: 50, offset: 0, summary: { ...summary, spend: 10 } }, { items: [], total: 0, limit: 50, offset: 0, summary: { ...summary, currency: 'VND' } }])('rejects malformed API success %# rather than displaying an empty ledger', (value) => { expect(() => decodeList(value)).toThrow('数据格式不符合约定'); });
it('rejects option missing arrays and numeric external IDs', () => {
  expect(() => decodeOptions({ sellers: [], advertisers: [], min_day: null, max_day: null })).toThrow();
  expect(() => decodeOptions({ sellers: [{ seller_id: 9007199254740992, shop_name: null, row_count: 1 }], advertisers: [], endpoints: [], min_day: null, max_day: null })).toThrow();
});
it('does not invent a nonnegative eligibility policy for server source order counts', () => {
  const data = decodeList({ items: [], total: 0, limit: 50, offset: 0, summary: { ...summary, attributed_orders: -2 } });
  expect(data.summary.attributed_orders).toBe(-2);
});
const identityRow = {
  id: 7, shop_pk: null, seller_id: 'TEST_SELLER', shop_name: null, advertiser_id: 'TEST_ADV', campaign_id: 'TEST_CAMPAIGN',
  product_id: 'TEST_PRODUCT', product_title: null, endpoint: '/TEST/ad', day: null, mixed_real_cost: '0.0000',
  onsite_roi2_shopping_sku: -2, onsite_roi2_shopping_value: '0.0000', onsite_mixed_real_roi2_shopping: null,
  metrics_extra: {}, created_at: null, updated_at: null,
};
function decodeIdentityRow(overrides: Record<string, unknown>) {
  return decodeList({ items: [{ ...identityRow, ...overrides }], total: 1, limit: 50, offset: 0, summary }).items[0];
}
it('preserves exact positive decimal-string PKs and existing safe numeric PKs without changing other fields', () => {
  expect(decodeIdentityRow({ id: '9007199254740993', shop_pk: '9223372036854775807' })).toEqual({ ...identityRow, id: '9007199254740993', shop_pk: '9223372036854775807' });
  expect(decodeIdentityRow({ id: 7, shop_pk: 42 })).toEqual({ ...identityRow, shop_pk: 42 });
  expect(decodeIdentityRow({ id: '7', shop_pk: null }).id).toBe('7');
});
it.each([9007199254740992, -1, 1.5, '0', '-1', '1.5', '1e3', '', ' 7', '7 ', '007'])('rejects rounded or noncanonical identity %s', (value) => {
  expect(() => decodeIdentityRow({ id: value })).toThrow('数据格式不符合约定');
  expect(() => decodeIdentityRow({ shop_pk: value })).toThrow('数据格式不符合约定');
});
it('does not widen safe numeric counters or signed source orders to identity strings', () => {
  expect(() => decodeList({ items: [], total: '9007199254740993', limit: 50, offset: 0, summary })).toThrow();
  expect(() => decodeIdentityRow({ onsite_roi2_shopping_sku: '-2' })).toThrow();
  expect(() => decodeIdentityRow({ onsite_roi2_shopping_sku: 9007199254740992 })).toThrow();
});
