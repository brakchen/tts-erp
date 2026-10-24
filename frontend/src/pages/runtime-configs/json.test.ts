import { describe, expect, it } from 'vitest';
import { decodeDetail } from './contracts';
import { parseObject, parseRuntime, stringifyRuntime } from './json';

describe('runtime-configs lossless JSON boundary', () => {
  it('roundtrips high precision decimals and large integer payload tokens without string coercion', () => {
    const text = '{"integer":900719925474099312345,"decimal":0.123456789012345678901,"zero":0,"nil":null,"negative":-0.0000000000000000000123456789}';
    expect(stringifyRuntime(parseObject(text))).toBe(text);
  });
  it('converts only safe version metadata and retains exact payload decimals', () => {
    const detail = decodeDetail(parseRuntime('{"configKey":"test.flags","displayName":"TEST","publishedVersion":1,"hasDraft":true,"updatedAt":"TEST","publishedAt":null,"retiredAt":null,"draftVersion":3,"jsonSchema":{},"publishedPayload":null,"draftPayload":{"ratio":0.123456789012345678901}}'));
    expect(detail.draftVersion).toBe(3);
    expect(stringifyRuntime(detail.draftPayload)).toBe('{"ratio":0.123456789012345678901}');
  });
});
