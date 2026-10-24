import { expect, it } from 'vitest';
import { displayDecimal, naturalOrder, queryParams, readQuery, sorts } from './query';
it('bounds URL pagination and sort while preserving exact external filters', () => {
  const q = readQuery('?seller_id=900719925474099312345&advertiser_id=TEST&endpoint=%2FTEST%3Fx%3D1%26y%3D2&q=TEST_%25&sort=__proto__&order=bad&limit=500&offset=1.1');
  expect(q).toMatchObject({ seller_id: '900719925474099312345', endpoint: '/TEST?x=1&y=2', q: 'TEST_%', sort: 'day', order: 'desc', limit: 50, offset: 0 });
  expect(readQuery('?offset=9007199254740993').offset).toBe(0);
  expect(readQuery('?offset=-1').offset).toBe(0);
  expect(readQuery(`?${queryParams(q)}`)).toEqual(q);
});
it.each(sorts)('serializes both sort and order for %s', (sort) => {
  const params = queryParams({ ...readQuery(''), sort, order: naturalOrder(sort) });
  expect(params.get('sort')).toBe(sort); expect(params.get('order')).toBe(['shop','campaign','product'].includes(sort) ? 'asc' : 'desc');
});
it.each([
  ['9999999999999999.9999', '9,999,999,999,999,999.9999'], ['0.0000', '0.0000'], ['-1234.5000', '-1,234.5000'], [null, '—'], [0, '0'],
] as const)('groups source %s without precision loss or rounding', (value, expected) => { expect(displayDecimal(value)).toBe(expected); });
