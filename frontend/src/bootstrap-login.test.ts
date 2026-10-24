import { afterEach, expect, it } from 'vitest';
import { readBootstrap } from './bootstrap';

const base = { rootPath: '/tts', pageId: 'login', navigation: [], releaseId: 'TEST_login', roleCapabilities: { readwrite: false, admin: false }, loginNextPath: '/tts/v2/pages/dashboard?tab=TEST' };
function install(value: unknown) {
  const script = document.createElement('script'); script.id = 'frontend-bootstrap'; script.type = 'application/json'; script.textContent = JSON.stringify(value); document.body.appendChild(script);
}
afterEach(() => document.getElementById('frontend-bootstrap')?.remove());

it.each(['', '/tts'])('keeps the canonical anonymous next path exactly once under %s', (rootPath) => {
  const loginNextPath = `${rootPath}/v2/pages/dashboard?tab=TEST`;
  install({ ...base, rootPath, loginNextPath });
  expect(readBootstrap()).toMatchObject({ pageId: 'login', navigation: [], loginNextPath, roleCapabilities: { readwrite: false, admin: false } });
});
it.each([undefined, '', '//TEST.invalid/', 'https://TEST.invalid/', '/v2/pages/dashboard', '/tts/../admin', '/tts/\\TEST.invalid', '/%2f%2fTEST.invalid'])('refuses missing, external, traversal or unprefixed login next paths %#', (loginNextPath) => {
  install({ ...base, loginNextPath });
  expect(() => readBootstrap()).toThrow();
});
it('does not create an anonymous navigation or write-capability authority', () => {
  install({ ...base, navigation: [{ id: 'dashboard', label: 'TEST dashboard', group: 'TEST', href: '/tts/v2/pages/dashboard' }] });
  expect(() => readBootstrap()).toThrow();
});
it('rejects elevated hints on an anonymous login document', () => {
  install({ ...base, roleCapabilities: { readwrite: true, admin: true } });
  expect(() => readBootstrap()).toThrow();
});
