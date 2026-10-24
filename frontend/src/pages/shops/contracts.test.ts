import { describe, expect, it } from 'vitest';
const modulePath = './contracts';
const shop = { id: 41, platform: 'tiktok', shop_id: '90071992547409931234567', account_name: 'TEST_name', region: null, seller_type: null, status: 'active', opened_date: null, credential_id: null, service_id: 'TEST_svc', app_credentials_configured: true };

describe('shop wire decoding', () => {
  it('preserves external IDs/null dates/internal PK and does not confuse App configuration with OAuth', async () => {
    const { decodeShop, decodeShopList } = await import(modulePath);
    expect(decodeShop(shop)).toEqual(shop); expect(decodeShopList([shop])).toEqual([shop]);
    expect(decodeShop({ ...shop, app_secret: 'TEST_SECRET' })).not.toHaveProperty('app_secret');
  });
  it.each([null, {}, { ...shop, id: '41' }, { ...shop, shop_id: Number.MAX_SAFE_INTEGER + 1 }, { ...shop, credential_id: '3' }, { ...shop, app_credentials_configured: 'true' }, { ...shop, opened_date: '2026-02-31' }])('rejects malformed required shape %#', async (value) => {
    const { decodeShop } = await import(modulePath); expect(() => decodeShop(value)).toThrow();
  });
  it('does not fabricate totals from the page when the count header is absent or malformed', async () => {
    const { decodeTotal } = await import(modulePath);
    for (const value of [null, '', 'n/a', '-1', '2.4', '9007199254740993']) expect(decodeTotal(value)).toBeNull();
    expect(decodeTotal('0')).toBe(0); expect(decodeTotal('502')).toBe(502);
  });
  it('requires the contracted command body and requested identity, not just successful HTTP', async () => {
    const { decodeShopCommand } = await import(modulePath);
    expect(decodeShopCommand({ shop }, { id: 41 })).toEqual(shop);
    expect(() => decodeShopCommand({ shop }, { id: 42 })).toThrow();
    expect(() => decodeShopCommand({ shop }, { shop_id: shop.shop_id, register: true })).toThrow();
    expect(decodeShopCommand({ created: false, shop }, { shop_id: shop.shop_id, register: true })).toEqual(shop);
    expect(() => decodeShopCommand({ created: true, shop }, { shop_id: 'TEST_wrong', register: true })).toThrow();
    expect(() => decodeShopCommand(null, { id: 41 })).toThrow();
  });
  it('accepts contracted authorize_url without invented body.ok while refusing executable URLs and bad expiry', async () => {
    const { decodeAuthorization } = await import(modulePath);
    const link = { authorize_url: 'https://services.tiktokshop.com/open/authorize?state=TEST_one', state_expires_at: '2026-10-24T20:00:00+00:00' };
    expect(decodeAuthorization(link)).toEqual(link);
    for (const bad of [null, {}, { ...link, authorize_url: 'javascript:alert(1)' }, { ...link, authorize_url: '/TEST/relative' }, { ...link, state_expires_at: 'tomorrow' }]) expect(() => decodeAuthorization(bad)).toThrow();
    for (const state_expires_at of ['2026-10-24', '2026-10-24T20:00:00', '2026-02-31T20:00:00+00:00']) expect(() => decodeAuthorization({ ...link, state_expires_at })).toThrow();
    expect(decodeAuthorization({ ...link, state_expires_at: '2020-01-01T00:00:00.123456Z' }).state_expires_at).toBe('2020-01-01T00:00:00.123456Z');
  });
});
