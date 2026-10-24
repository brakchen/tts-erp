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
