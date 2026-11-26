import { expect, it } from 'vitest';
import { decodeShops as decodeCosts } from '../pages/manual-costs/contracts';
import { decodeShops as decodeProfitability } from '../pages/spu-profitability/contracts';
import { decodeShops as decodeComparison } from '../pages/spu-profit-deterioration/contracts';

it.each([decodeCosts, decodeProfitability, decodeComparison])('retains alias and platform identity through shop decoding', (decode) => {
  const shop = { id: '41', shop_id: '90071992547409931', account_name: 'TEST_platform', shop_alias: 'TEST_alias', region: 'VN' };
  expect(decode([shop])[0]).toMatchObject({ id: '41', shop_id: shop.shop_id, account_name: shop.account_name, shop_alias: shop.shop_alias });
  expect(() => decode([{ ...shop, shop_alias: {} }])).toThrow();
});
