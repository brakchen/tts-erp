export interface Shop {
  id: number; platform: string; shop_id: string;
  account_name: string | null; region: string | null; seller_type: string | null; status: string | null;
  opened_date: string | null; credential_id: number | null; service_id: string | null; app_credentials_configured: boolean;
}
export interface AuthorizationLink { authorize_url: string; state_expires_at: string }
const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const positiveId = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
const nullableString = (value: unknown): value is string | null => value === null || typeof value === 'string';
const bad = () => new Error('店铺响应格式错误');
function dateOnly(value: unknown): value is string | null {
  if (value === null) return true;
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const time = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value;
}
export function decodeShop(value: unknown): Shop {
  if (!record(value) || !positiveId(value.id) || typeof value.platform !== 'string' || typeof value.shop_id !== 'string' ||
      !['account_name', 'region', 'seller_type', 'status', 'service_id'].every((key) => nullableString(value[key])) ||
      !dateOnly(value.opened_date) || !(value.credential_id === null || positiveId(value.credential_id)) || typeof value.app_credentials_configured !== 'boolean') throw bad();
  // Explicit projection keeps unknown/secret response fields out of UI state.
  return { id: value.id, platform: value.platform, shop_id: value.shop_id,
    account_name: value.account_name as string | null, region: value.region as string | null,
    seller_type: value.seller_type as string | null, status: value.status as string | null,
    service_id: value.service_id as string | null, opened_date: value.opened_date,
    credential_id: value.credential_id, app_credentials_configured: value.app_credentials_configured };
}
export function decodeShopList(value: unknown): Shop[] {
  if (!Array.isArray(value)) throw bad();
  return value.map(decodeShop);
}
export function decodeTotal(value: string | null): number | null {
  if (value === null || !/^\d+$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}
export function decodeShopCommand(value: unknown, expected: { id?: number; shop_id?: string; register?: boolean }): Shop {
  if (!record(value) || (expected.register && typeof value.created !== 'boolean')) throw bad();
  const shop = decodeShop(value.shop);
  if ((expected.id !== undefined && shop.id !== expected.id) || (expected.shop_id !== undefined && shop.shop_id !== expected.shop_id)) throw bad();
  return shop;
}
export function decodeAuthorization(value: unknown): AuthorizationLink {
  if (!record(value) || typeof value.authorize_url !== 'string' || typeof value.state_expires_at !== 'string') throw new Error('授权链接响应格式错误');
  const timestamp = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(value.state_expires_at);
  if (!timestamp || timestamp[1].startsWith('0000') || !dateOnly(timestamp[1]) || Number(timestamp[2]) > 23 || Number(timestamp[3]) > 59 || Number(timestamp[4]) > 59 || Number(timestamp[5] ?? 0) > 23 || Number(timestamp[6] ?? 0) > 59 || !Number.isFinite(Date.parse(value.state_expires_at))) throw new Error('授权链接响应格式错误');
  // Representation check only: expiry/eligibility remains server-owned, not browser-clock policy.
  let url: URL;
  try { url = new URL(value.authorize_url); } catch { throw new Error('授权链接响应格式错误'); }
  if (url.protocol !== 'https:' || url.username || url.password) throw new Error('授权链接响应格式错误');
  return { authorize_url: value.authorize_url, state_expires_at: value.state_expires_at };
}
