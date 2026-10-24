import { describe, expect, it } from 'vitest';
import { pageLoader } from './page-loader';

describe('page module mapping (not activation or authorization)', () => {
  it('loads the stats content without adding a permission or route authority', async () => {
    const module = await pageLoader('intercept-stats')();
    expect(typeof module.default).toBe('function');
  });
  it('loads shops content without activating the backend route', async () => {
    const module = await pageLoader('shops')();
    expect(typeof module.default).toBe('function');
  });
  it('loads enum content without activating or authorizing its backend route', async () => {
    const module = await pageLoader('enum-map')(); expect(typeof module.default).toBe('function');
  });
  it.each(['ad-daily', 'sync-jobs'])('loads the reviewed %s content through the existing loader', async (id) => {
    const module = await pageLoader(id)();
    expect(typeof module.default).toBe('function');
  });
  it('still rejects unknown and prototype names', () => {
    for (const id of ['TEST_missing', '__proto__', 'constructor']) expect(() => pageLoader(id)).toThrow('当前页面未提供前端模块');
  });
});
