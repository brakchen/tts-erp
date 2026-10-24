import { fireEvent, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';

// Baseline driver exercises the current legacy page before the content-component migration.
async function mount() {
  const html = readFileSync('../tts_erp_v2/templates/pages/runtime-configs.html', 'utf8');
  const fixture = new DOMParser().parseFromString(html, 'text/html').querySelector('main')!;
  document.body.replaceChildren(fixture);
  class Editor {
    text = '';
    setText(value: string) { this.text = value; }
    getText() { return this.text; }
    setMode() {} refresh() {}
  }
  Object.assign(window, { JSONEditor: Editor });
  window.eval(readFileSync('../tts_erp_v2/static/js/runtime-configs.js', 'utf8'));
  await waitFor(() => expect(document.getElementById('rc-secrets')).not.toHaveAttribute('hidden'));
  await new Promise((resolve) => setTimeout(resolve, 30));
}
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.replaceChildren(); });
describe('runtime secret command safety', () => {
  it('sends only one secret PUT while the first result remains pending', async () => {
    const writes: RequestInit[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
      if (url.endsWith('/auth/me')) return json({ role: 'readwrite' });
      if (init.method === 'PUT') { writes.push(init); return new Promise<Response>(() => {}); }
      return json({ items: [] });
    }));
    await mount();
    const form = document.getElementById('rc-secret-form')!;
    fireEvent.change(document.getElementById('rc-secret-name')!, { target: { value: 'test_token' } });
    fireEvent.change(document.getElementById('rc-secret-value')!, { target: { value: 'TEST_secret' } });
    fireEvent.submit(form); fireEvent.submit(form);
    await waitFor(() => expect(writes).toHaveLength(1));
  });
});
