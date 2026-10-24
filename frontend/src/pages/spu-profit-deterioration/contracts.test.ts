import { expect, it } from 'vitest';
import { parseIdentityJson } from '../../shared/api/identity-json';
import { decodeComparison } from './contracts';
import { displayChange } from './ComparisonTable';
import { queryError, queryParams, readQuery } from './query';
import { testItem, testPayload } from './fixtures';
it('preserves wire monetary strings and numeric bigint identities before rounding', () => {
  const raw = JSON.stringify(testPayload).replace('"shopPk":11', '"shopPk":9223372036854775807').replace('"spuPk":22', '"spuPk":9007199254740993');
  const decoded = decodeComparison(parseIdentityJson(raw));
  expect(decoded.items[0].shopPk).toBe('9223372036854775807');
  expect(decoded.items[0].spuPk).toBe('9007199254740993');
  expect(decoded.items[0].previousNetProfitCny).toBe('9999999999999999.99');
  expect(decoded.items[0].currentRoi).toBe('0');
  expect(decoded.items[0].previousOrderCount).toBeNull();
  expect(decoded.totals.currentOrderCount).toBe(5);
});
it.each([null, {}, { ...testPayload, total: null }, { ...testPayload, meta: { ...testPayload.meta, periods: null } }, { ...testPayload, items: [{ ...testItem, currentRoi: 0 }] }, { ...testPayload, items: [{ ...testItem, currentNoOrderAdSpend: undefined }] }, { ...testPayload, items: [{ ...testItem, spuPk: 9007199254740992 }] }])('fails closed on malformed success rather than hiding unavailable facts %#', (value) => {
  expect(() => decodeComparison(value)).toThrow('数据格式不符合约定');
});
it('keeps display changes separate, nonpositive denominators unavailable, unsafe magnitudes unrounded', () => {
  expect(displayChange('2', '2.5')).toMatchObject({ difference: '+0.5', percent: '+25%', direction: '↑ 上升' });
  expect(displayChange('100', '80', 'CNY')).toMatchObject({ difference: '−20 CNY', percent: '−20%', direction: '↓ 下降' });
  expect(displayChange('-0.5', '-0.2')).toMatchObject({ difference: '+0.3', percent: '—' });
  expect(displayChange('0', '0')).toMatchObject({ difference: '0', percent: '—', direction: '→ 持平' });
  expect(displayChange(null, '0').difference).toBe('—');
  expect(displayChange('9999999999999999.99', '0')).toMatchObject({ difference: '—', percent: '—' });
});
it.each(['?spu_ids=', '?shop_pk=', '?anchor_date=', '?window_days=1', '?window_days=3&window_days=7', '?spu_ids=22,,33', '?limit=501', '?offset=9007199254740993', '?anchor_date=2026-02-30'])('does not widen malformed URL scope %s', (search) => {
  expect(queryError(readQuery(search))).not.toBe('');
});
it('forwards only supported parameters and preserves repeated exact IDs/date with no browser date derivation', () => {
  const q = readQuery('?shop_pk=11&spu_ids=9007199254740993&spu_ids=22&window_days=3&anchor_date=2026-09-30&activity=all&limit=50&offset=100');
  expect(queryError(q)).toBe('');
  expect(queryParams(q).getAll('spu_ids')).toEqual(['9007199254740993', '22']);
  expect(queryParams(q).get('anchor_date')).toBe('2026-09-30');
  expect(queryError(q, { shop: '11', date: '2026-09-29' })).toContain('2026-09-29');
});
it('keeps reset URL defaults absent rather than introducing an invalid empty shop parameter', () => {
  const params = queryParams(readQuery(''));
  expect(params.has('shop_pk')).toBe(false);
  expect(params.has('anchor_date')).toBe(false);
  expect(queryError(readQuery(`?${params}`))).toBe('');
});
