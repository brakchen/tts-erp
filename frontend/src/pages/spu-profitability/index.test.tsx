import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Page from './index';
import type { FrontendBootstrap } from '../../shared/contracts';

vi.mock('./ProfitabilityTable', () => ({ ProfitabilityTable: ({ rows }: { rows: { spu_id: string }[] }) => <div aria-label="TEST 盈利行">{rows.map((row) => <span key={row.spu_id}>{row.spu_id}</span>)}</div> }));
const bootstrap: FrontendBootstrap = { pageId: 'spu-roi', rootPath: '', releaseId: 'TEST_release', navigation: [], roleCapabilities: { readwrite: true, admin: false } };
const shop = { id: 7, shop_id: '90071992547409931', account_name: 'TEST 店铺', region: 'VN' };
const product = { spu_pk: 101, spu_id: 'TEST_OLD', title: 'TEST 原商品', status: 'ACTIVATE', main_image_url: null };
const payload = (id = 'TEST_RESULT') => ({ items: [{ ...product, spu_id: id }], total: 1, totals: { net_profit: '12.0000', spend: '3.0000' }, meta: {} });
let calls: { url: string; init?: RequestInit }[];
let stored: { spuId: string; title: string }[];
let patchFailure: number | undefined;
function response(data: unknown, status = 200) { return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }); }
beforeEach(() => {
  calls = []; stored = [{ spuId: 'TEST_OLD', title: 'TEST 原商品' }]; patchFailure = undefined; localStorage.clear(); history.replaceState(null, '', '/v2/pages/spu-roi?shop_pk=7');
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); calls.push({ url, init });
    if (url.includes('channel-accounts')) return response([shop]);
    if (url.includes('channel-product-options')) return response([{ spu_id: 'TEST_NEW', title: 'TEST 新商品', status: 'ACTIVATE' }]);
    if (url.includes('/reporting/focused-spus/')) {
      if (init?.method === 'PATCH') {
        if (patchFailure) return response({ detail: 'TEST_failure' }, patchFailure);
        const body = JSON.parse(String(init.body)); stored = stored.filter((item) => !body.removeSpuIds.includes(item.spuId)); for (const id of body.addSpuIds) stored.push({ spuId: id, title: id });
        return response({ shopPk: 7, total: stored.length, addedSpuIds: body.addSpuIds, removedSpuIds: body.removeSpuIds });
      }
      return response({ shopPk: 7, items: stored, total: stored.length, matchedTotal: stored.length, limit: 500, offset: 0 });
    }
    if (url.includes('/analytics/spu-roi?')) return response(payload());
    return response({});
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('both page profiles render through the same React owner and keep the server scope', async () => {
  render(<Page bootstrap={{ ...bootstrap, pageId: 'focused-spus' }} />);
  await screen.findByText('TEST_RESULT');
  const reads = calls.filter((call) => call.url.includes('/analytics/spu-roi?'));
  expect(reads.length).toBeGreaterThan(0); const query = new URL(reads.at(-1)!.url, location.origin).searchParams;
  expect(query.get('scope')).toBe('focused'); expect(query.has('spu_ids')).toBe(false); expect(query.has('w_start')).toBe(false);
  expect(calls.filter((call) => call.url.includes('channel-accounts'))).toHaveLength(1);
});
it('adding one focused search result never implicitly removes existing subscriptions', async () => {
  render(<Page bootstrap={{ ...bootstrap, pageId: 'focused-spus' }} />);
  await screen.findByRole('button', { name: '移除 SPU TEST_OLD' });
  fireEvent.change(screen.getByLabelText('按 SPU ID / 标题搜索'), { target: { value: 'TEST 新' } });
  const option = await screen.findByRole('checkbox', { name: /TEST_NEW/ }); fireEvent.click(option);
  await waitFor(() => expect(calls.filter((call) => call.init?.method === 'PATCH')).toHaveLength(1));
  const patch = calls.find((call) => call.init?.method === 'PATCH')!;
  expect(JSON.parse(String(patch.init?.body))).toEqual({ addSpuIds: ['TEST_NEW'], removeSpuIds: [] });
  expect(patch.init?.headers && new Headers(patch.init.headers).get('X-Requested-With')).toBe('tts-erp');
  expect(screen.getByRole('button', { name: '移除 SPU TEST_OLD' })).toBeInTheDocument();
});
it('a failed focused mutation preserves selection and requires reading facts before another write', async () => {
  patchFailure = 500; render(<Page bootstrap={{ ...bootstrap, pageId: 'focused-spus' }} />);
  const remove = await screen.findByRole('button', { name: '移除 SPU TEST_OLD' }); fireEvent.click(remove);
  const retryRead = await screen.findByRole('button', { name: '重新读取关注事实' });
  expect(screen.getByRole('button', { name: '移除 SPU TEST_OLD' })).toBeDisabled();
  expect(calls.filter((call) => call.init?.method === 'PATCH')).toHaveLength(1);
  fireEvent.click(retryRead);
  await waitFor(() => expect(screen.getByRole('button', { name: '移除 SPU TEST_OLD' })).toBeEnabled());
  expect(calls.filter((call) => call.init?.method === 'PATCH')).toHaveLength(1);
});
it('standard default uses the shop yesterday while explicit invalid drill dates reach validation unchanged', async () => {
  history.replaceState(null, '', '/v2/pages/spu-roi?shop_pk=7&w_start=bad-date&w_end=2026-09-30&spu_ids=90071992547409931');
  render(<Page bootstrap={bootstrap} />); await screen.findByText('TEST_RESULT');
  const last = calls.filter((call) => call.url.includes('/analytics/spu-roi?')).at(-1)!;
  const params = new URL(last.url, location.origin).searchParams;
  expect(params.get('w_start')).toBe('bad-date'); expect(params.get('w_end')).toBe('2026-09-30'); expect(params.get('spu_ids')).toBe('90071992547409931');
});
it('inverted date edits retain both inputs without querying a widened range', async () => {
  history.replaceState(null, '', '/v2/pages/spu-roi?shop_pk=7&w_start=2026-09-01&w_end=2026-09-30');
  render(<Page bootstrap={bootstrap} />); await screen.findByText('TEST_RESULT');
  fireEvent.change(screen.getByLabelText('销售/退款起始日期（空 = 不限）'), { target: { value: '2026-10-01' } });
  expect(screen.getByLabelText('销售/退款起始日期（空 = 不限）')).toHaveValue('2026-10-01');
  expect(screen.getByLabelText('销售/退款截止日期（空 = 不限）')).toHaveValue('2026-09-30');
  expect(screen.getByRole('alert')).toHaveTextContent('起始日期不能晚于截止日期');
});
