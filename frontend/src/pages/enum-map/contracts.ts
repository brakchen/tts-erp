export interface EnumRow { id: number; enum_type: string; enum_value: string; label_zh: string; sort_order: number }
const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const integer = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value);
export function decodeEnumList(value: unknown): EnumRow[] {
  if (!Array.isArray(value)) throw new Error('枚举列表响应格式错误');
  return value.map((row: unknown) => {
    if (!record(row) || !integer(row.id) || row.id <= 0 || typeof row.enum_type !== 'string' || typeof row.enum_value !== 'string' || typeof row.label_zh !== 'string' || !integer(row.sort_order) || row.sort_order < 0) throw new Error('枚举列表响应格式错误');
    return { id: row.id, enum_type: row.enum_type, enum_value: row.enum_value, label_zh: row.label_zh, sort_order: row.sort_order };
  });
}
/** Input-format validation only. The backend owns ordering and all business decisions. */
export function parseSortOrder(value: string): number {
  if (!/^\d+$/.test(value)) throw new Error('排序必须是非负整数');
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) throw new Error('排序超出安全整数范围');
  return parsed;
}
export function decodeEnumDelete(value: unknown, expectedId: number): number {
  if (!record(value) || !integer(value.deleted) || value.deleted !== expectedId) throw new Error('枚举删除响应格式错误');
  return value.deleted;
}
export function decodeEnumUpsert(value: unknown): { id: number; action: 'created' | 'updated' } {
  if (!record(value) || !integer(value.id) || value.id <= 0 || (value.action !== 'created' && value.action !== 'updated')) throw new Error('枚举保存响应格式错误');
  return { id: value.id, action: value.action };
}
