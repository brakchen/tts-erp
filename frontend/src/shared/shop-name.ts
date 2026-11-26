/** View-only shop label; IDs and the platform name retain their own values. */
export interface ShopName {
  account_name?: string | null;
  shop_alias?: string | null;
}

export function displayShopName(shop: ShopName | null | undefined, fallback = ''): string {
  return shop?.shop_alias || shop?.account_name || fallback;
}

export function decodeShopAlias(value: unknown): string | null {
  if (value == null) return null;
  if (typeof value !== 'string') throw new Error('店铺别名格式错误');
  return value;
}
