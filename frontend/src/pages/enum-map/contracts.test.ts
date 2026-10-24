import { describe, expect, it } from 'vitest';
const modulePath = './contracts';
const row = { id: 41, enum_type: 'TEST_unknown_type', enum_value: 'TEST_ENGLISH', label_zh: 'TEST_中文', sort_order: 2 };

describe('enum wire/input boundary', () => {
  it('retains snake_case, unknown categories and backend row order without recalculating or sorting', async () => {
    const { decodeEnumList } = await import(modulePath);
    const rows = [row, { ...row, id: 40, sort_order: 1 }];
    expect(decodeEnumList(rows)).toEqual(rows);
    expect(decodeEnumList([{ ...row, secret: 'TEST_drop' }])[0]).not.toHaveProperty('secret');
  });
  it.each([null, {}, [null], [{ ...row, id: '41' }], [{ ...row, sort_order: 1.5 }], [{ ...row, sort_order: -1 }], [{ ...row, label_zh: null }]])('rejects malformed list instead of manufacturing an empty category or zero %#', async (value) => {
    const { decodeEnumList } = await import(modulePath); expect(() => decodeEnumList(value)).toThrow();
  });
  it.each(['', '-1', '2.4', '1x', 'Infinity', '1e3', '9007199254740993'])('does not truncate/coerce invalid sort input %s', async (value) => {
    const { parseSortOrder } = await import(modulePath); expect(() => parseSortOrder(value)).toThrow();
  });
  it('accepts genuine nonnegative integer input, including zero, without monetary or threshold logic', async () => {
    const { parseSortOrder } = await import(modulePath); expect(parseSortOrder('0')).toBe(0); expect(parseSortOrder('23')).toBe(23);
  });
  it('checks contracted upsert metadata after HTTP success, not alternate status fields', async () => {
    const { decodeEnumUpsert } = await import(modulePath);
    expect(decodeEnumUpsert({ id: 41, action: 'created' })).toEqual({ id: 41, action: 'created' });
    expect(decodeEnumUpsert({ id: 41, action: 'updated' })).toEqual({ id: 41, action: 'updated' });
    for (const bad of [null, {}, { ok: true }, { id: 41, action: 'ok' }, { id: '41', action: 'created' }]) expect(() => decodeEnumUpsert(bad)).toThrow();
  });
});
