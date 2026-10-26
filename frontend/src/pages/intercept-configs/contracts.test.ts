import { describe, expect, it } from 'vitest';
import { batchBody, configDetail, configInput, configList, importInputs, parseIdentityJson } from './contracts';
const input = { domain: 'TEST.example', endpoint: '/TEST/*', mode: 'whitelist', capture_headers: false, capture_body: false, description: null, tags: [], enabled: false };
const row = { ...input, id: 42, created_at: null, updated_at: null };
describe('intercept-configs contracts', () => {
  it('preserves huge numeric identity before rounding and serializes exact integer batch tokens', () => {
    const parsed = configList(parseIdentityJson(`{"configs":[${JSON.stringify(row).replace('"id":42', '"id":9007199254740993123')}],"total":0}`));
    expect(parsed.configs[0].id).toBe('9007199254740993123'); expect(parsed.total).toBe('0'); expect(parsed.configs[0].enabled).toBe(false); expect(parsed.configs[0].description).toBeNull();
    expect(batchBody('delete', parsed.configs.map((item) => item.id))).toBe('{"action":"delete","ids":[9007199254740993123]}');
  });
  it.each([0, -1, 1.5, 9007199254740992, '01', '1e3', '1,2', null])('rejects invalid or previously rounded identity %s', (id) => {
    expect(() => configDetail({ config: { ...row, id } })).toThrow(); expect(() => batchBody('delete', [id as string])).toThrow();
  });
  it('requires real envelopes and checks exact selected identity', () => {
    expect(() => configDetail(row)).toThrow(); expect(() => configDetail({ config: row }, '43')).toThrow();
    expect(() => configList({ configs: [row, row], total: 2 })).toThrow(); expect(() => configList({ configs: [], total: null })).toThrow(); expect(() => configList({ total: 0 })).toThrow();
    expect(() => configList({ configs: [{ ...row, capture_body: undefined }], total: 1 })).toThrow();
  });
  it('accepts array/envelope import with server defaults, keeps null/empty/false distinct and excludes exported identity', () => {
    expect(importInputs('[{"domain":"TEST","endpoint":"/"}]')[0]).toEqual({ domain: 'TEST', endpoint: '/', mode: 'whitelist', capture_headers: true, capture_body: true, description: null, tags: [], enabled: true });
    expect(importInputs(JSON.stringify({ configs: [{ ...row, description: '' }] }))).toEqual([{ ...input, description: '' }]);
  });
  it.each([{ mode: null }, { mode: 'unknown' }, { enabled: null }, { capture_headers: 0 }, { capture_body: '' }, { tags: null }, { tags: [1] }, { description: 0 }])('rejects invalid import fields instead of coercing %j', (fields) => {
    expect(() => importInputs(JSON.stringify([{ ...input, ...fields }]))).toThrow();
  });
  it('enforces backend Unicode codepoint limits rather than byte/UTF-16 counts', () => {
    expect(configInput({ ...input, domain: '测'.repeat(255), endpoint: '🛍'.repeat(512) }).endpoint).toHaveLength(1024);
    expect(() => configInput({ ...input, domain: '测'.repeat(256) })).toThrow(); expect(() => configInput({ ...input, endpoint: '🛍'.repeat(513) })).toThrow();
  });
});
