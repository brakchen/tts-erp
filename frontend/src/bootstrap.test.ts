import { afterEach, describe, expect, it } from 'vitest';
import { readBootstrap } from './bootstrap';

const valid = { rootPath: '/tts', pageId: 'dashboard', releaseId: 'TEST-release', navigation: [{ id: 'dashboard', label: '控制台', group: '总览', href: '/tts/v2/pages/dashboard' }] };
function inject(value: unknown) {
  const script = document.createElement('script'); script.id = 'frontend-bootstrap'; script.type = 'application/json';
  script.textContent = JSON.stringify(value); document.body.append(script);
}
afterEach(() => { document.body.replaceChildren(); });

describe('readBootstrap', () => {
  it('consumes the backend-owned menu without recalculating permissions', () => {
    inject(valid); expect(readBootstrap()).toEqual(valid);
  });
  it.each([{ readwrite: true, admin: false }, { readwrite: false, admin: false }, { readwrite: true, admin: true }])('preserves backend role hints without deriving a hierarchy: %j', (roleCapabilities) => {
    inject({ ...valid, roleCapabilities });
    expect(readBootstrap()).toEqual({ ...valid, roleCapabilities });
  });
  it.each([null, [], true, {}, { readwrite: true }, { readwrite: 'true', admin: true }, { readwrite: true, admin: 1 }])('never enables writes from malformed hints %#', (roleCapabilities) => {
    inject({ ...valid, roleCapabilities });
    const result = readBootstrap() as unknown as { roleCapabilities?: { readwrite: boolean; admin: boolean } };
    expect(result.roleCapabilities).toBeUndefined();
  });
  it('does not interpret label text as markup', () => {
    inject({ ...valid, navigation: [{ ...valid.navigation[0], label: '<img src=x onerror=alert(1)>' }] });
    expect(readBootstrap().navigation[0].label).toContain('<img');
    expect(document.querySelector('img')).toBeNull();
  });
  it('fails explicitly when the data node is absent or invalid JSON', () => {
    expect(() => readBootstrap()).toThrow();
    inject(valid); document.getElementById('frontend-bootstrap')!.textContent = '{';
    expect(() => readBootstrap()).toThrow();
  });
  it.each([null, [], {}, { ...valid, navigation: null }, { ...valid, pageId: '' }, { ...valid, rootPath: '//evil.test' }, { ...valid, navigation: [{ ...valid.navigation[0], href: 'https://evil.test' }] }])('rejects malformed bootstrap %#', (value) => {
    inject(value); expect(() => readBootstrap()).toThrow();
  });
});
