import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { setUnauthorizedHandler } from '../../shared/api/client';
import type { PageProps } from '../../shared/contracts';
import Page from './index';

const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'sync-jobs', navigation: [], releaseId: 'TEST', roleCapabilities: { readwrite: true, admin: true } } };
const stamp = '2026-10-24T13:00:00Z';
function job(job_name = 'tiktok.orders', is_tiktok = true) {
  return { job_name, module_path: 'TEST_jobs.orders', entrypoint: 'run', interval_seconds: 3600, is_tiktok, enabled: false, scope_type: is_tiktok ? 'tiktok_shop' : 'system', last_status: { job_name, interval_seconds: 3600, last_run_at: stamp, last_finished_at: null, last_status: 'running', last_error: null, next_expected_at: stamp, lag_seconds: 0, cycles_late: 0, severity: 'crit' } };
}
const data = { server_time: stamp, jobs: [job(), job('TEST_system', false)], tiktok_shops: [{ shop_pk: 41, shop_id: '90071992547409931234', account_name: 'TEST_长店铺', status: 'active' }] };
const json = (value: unknown, status = 200, headers = {}) => new Response(JSON.stringify(value), { status, headers });
function mockApi(command: (url: string, init: RequestInit) => Promise<Response> = async () => json({}), read: () => Promise<Response> = async () => json(data)) {
  const fetcher = vi.fn((url: string, init: RequestInit) => init.method === 'GET' ? read() : command(url, init)); vi.stubGlobal('fetch', fetcher); return fetcher;
}
// Final behavior oracle always renders the recovered React page.
async function mount(bootstrap = props.bootstrap) {
  return render(<Page bootstrap={bootstrap} />);
}
const row = async (name = 'tiktok.orders') => (await screen.findByText(name)).closest('tr')!;
const writes = (fetcher: ReturnType<typeof mockApi>) => fetcher.mock.calls.filter(([, init]) => init.method !== 'GET');
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('sync-jobs content', () => {
  it('renders all server columns and severity without recalculating from zero lag, preserving server order', async () => {
    const fetcher = mockApi(); await mount(); const first = await row();
    expect(screen.getByRole('heading', { name: '任务清单' })).toBeVisible();
    expect(within(first).getByText('crit')).toBeVisible(); expect(within(first).getByText(/落后周期：0/)).toBeVisible();
    expect(within(first).getByText('TEST_jobs.orders · run')).toBeVisible(); expect(within(first).getByText('3600 秒')).toBeVisible();
    for (const name of ['任务', '周期', '范围', '状态', '上次运行', '周期调度', '立即执行']) expect(screen.getByRole('columnheader', { name })).toBeVisible();
    expect(screen.getByRole('region', { name: '任务表格' })).toBeVisible(); expect(fetcher.mock.calls[0][0]).toBe('/tts/v2/sync/jobs');
    expect(screen.getAllByRole('row')[1]).toHaveTextContent('tiktok.orders'); expect(writes(fetcher)).toHaveLength(0);
  });
  it.each([undefined, { readwrite: false, admin: false }])('fails closed for unauthorized bootstrap %# without reading management data', async (roleCapabilities) => {
    const fetcher = mockApi(); await mount({ ...props.bootstrap, roleCapabilities });
    expect(screen.getByText(/需要 readwrite 或 admin/)).toBeVisible(); expect(screen.getByRole('button', { name: '刷新任务' })).toBeDisabled(); expect(fetcher).not.toHaveBeenCalled();
  });
  it('separates readwrite trigger rights from admin enable rights, including disabled scheduled jobs', async () => {
    mockApi(); await mount({ ...props.bootstrap, roleCapabilities: { readwrite: true, admin: false } }); const first = await row();
    expect(within(first).getByRole('button', { name: '启用 tiktok.orders 周期调度' })).toBeDisabled(); expect(within(first).getByRole('button', { name: '执行 tiktok.orders' })).toBeEnabled();
  });
  it('enables exactly once using shared CSRF and accepts only matching server acknowledgement', async () => {
    let release!: (value: Response) => void; const fetcher = mockApi(async () => new Promise<Response>((resolve) => { release = resolve; })); await mount(); const first = await row();
    const button = within(first).getByRole('button', { name: '启用 tiktok.orders 周期调度' }); fireEvent.click(button); fireEvent.click(button);
    expect(writes(fetcher)).toHaveLength(1); const [url, init] = writes(fetcher)[0]; expect(url).toBe('/tts/v2/admin/sync-jobs/tiktok.orders/enabled'); expect(init.method).toBe('PATCH'); expect(JSON.parse(String(init.body))).toEqual({ enabled: true }); expect(new Headers(init.headers).get('X-Requested-With')).toBe('tts-erp');
    await act(async () => { release(json({ job_name: 'tiktok.orders', enabled: true, updated_at: stamp })); });
    expect(await screen.findByText(/周期调度变更已确认/)).toBeVisible(); expect(within(first).getByRole('button', { name: '停用 tiktok.orders 周期调度' })).toBeEnabled();
  });
  it('requires an accessible all-shop confirmation, cancel does not submit, accepted is not completed', async () => {
    const fetcher = mockApi(async () => json({ accepted: true, job_name: 'tiktok.orders', shop_id: null, accepted_at: stamp, message: 'TEST_raw_message' })); const user = userEvent.setup(); await mount(); const first = await row();
    await user.click(within(first).getByRole('button', { name: '执行 tiktok.orders' })); let dialog = screen.getByRole('dialog'); expect(within(dialog).getByText(/全部授权店铺.*上游同步/)).toBeVisible(); expect(writes(fetcher)).toHaveLength(0);
    await user.click(within(dialog).getByRole('button', { name: '取消' })); expect(writes(fetcher)).toHaveLength(0);
    await user.click(within(first).getByRole('button', { name: '执行 tiktok.orders' })); dialog = screen.getByRole('dialog'); await user.click(within(dialog).getByRole('button', { name: '确认全店铺执行' }));
    expect(await screen.findByText(/已提交后台，尚未确认完成/)).toBeVisible(); expect(JSON.parse(String(writes(fetcher)[0][1].body))).toEqual({}); expect(document.body.textContent).not.toContain('TEST_raw_message');
  });
  it('selects an exact long shop ID and sends only shop_id, while system trigger sends no shop', async () => {
    const fetcher = mockApi(async (url, init) => { const body = JSON.parse(String(init.body)); return json({ accepted: true, job_name: url.includes('TEST_system') ? 'TEST_system' : 'tiktok.orders', shop_id: body.shop_id ?? null, accepted_at: stamp, message: 'queued' }); }); const user = userEvent.setup(); await mount(); const first = await row();
    await user.click(within(first).getByRole('button', { name: /执行店铺/ })); await user.click(screen.getByRole('option', { name: /TEST_长店铺/ }));
    await user.click(within(first).getByRole('button', { name: '执行 tiktok.orders' })); await screen.findByText(/已提交后台，尚未确认完成/); expect(screen.queryByRole('dialog')).toBeNull(); expect(JSON.parse(String(writes(fetcher)[0][1].body))).toEqual({ shop_id: '90071992547409931234' });
    await user.click(within(await row('TEST_system')).getByRole('button', { name: '执行 TEST_system' })); expect(writes(fetcher)).toHaveLength(2); expect(JSON.parse(String(writes(fetcher)[1][1].body))).toEqual({});
  });
  it.each(['network', 'mismatch', 'server'])('gates uncertain %s outcome until explicit GET then explicit retry, never proves a trigger committed', async (mode) => {
    const fetcher = mockApi(async () => { if (mode === 'network') throw new TypeError('TEST_secret_network'); return mode === 'mismatch' ? json({ accepted: true, job_name: 'OTHER', shop_id: null, accepted_at: stamp, message: 'x' }) : json({}, 500); }); const user = userEvent.setup(); await mount(); const first = await row('TEST_system');
    await user.click(within(first).getByRole('button', { name: '执行 TEST_system' })); await screen.findByText(/提交结果未确认/);
    expect(within(first).getByRole('button', { name: '执行 TEST_system' })).toBeDisabled(); expect(writes(fetcher)).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: '查询核对' })); await screen.findByText(/不能证明原命令是否生效/); expect(writes(fetcher)).toHaveLength(1);
    expect(screen.getByRole('button', { name: '核对后明确重试原操作' })).toBeEnabled(); await user.click(screen.getByRole('button', { name: '核对后明确重试原操作' })); expect(writes(fetcher)).toHaveLength(2); expect(document.body.textContent).not.toContain('TEST_secret_network');
  });
  it('does not unlock an uncertain command after malformed verification data', async () => {
    let reads = 0; const fetcher = mockApi(async () => json({}, 500), async () => json(++reads === 1 ? data : { ...data, jobs: [] })); const user = userEvent.setup(); await mount(); await user.click(within(await row('TEST_system')).getByRole('button', { name: '执行 TEST_system' })); await screen.findByText(/提交结果未确认/);
    await user.click(screen.getByRole('button', { name: '查询核对' })); await screen.findByText(/核对未完成/); expect(screen.queryByRole('button', { name: '核对后明确重试原操作' })).toBeNull(); expect(writes(fetcher)).toHaveLength(1);
  });
  it('retains server authorization denial across refresh and separates 403 from expired 401', async () => {
    const unauthorized = vi.fn(); const unset = setUnauthorizedHandler(unauthorized); const fetcher = mockApi(async () => json({ detail: 'TEST_raw_secret' }, 403)); const user = userEvent.setup(); await mount(); const first = await row('TEST_system');
    await user.click(within(first).getByRole('button', { name: '执行 TEST_system' })); await screen.findByText(/权限不足或安全检查未通过/); expect(unauthorized).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '刷新任务' })); await row('TEST_system'); expect(within(await row('TEST_system')).getByRole('button', { name: '执行 TEST_system' })).toBeDisabled(); expect(writes(fetcher)).toHaveLength(1); expect(document.body.textContent).not.toContain('TEST_raw_secret'); unset();
  });
  it('renders valid empty state, rejects malformed required job fields, and permits explicit read retry', async () => {
    let reads = 0; mockApi(undefined, async () => json(++reads === 1 ? { ...data, jobs: [{ ...job(), enabled: 'false' }] } : { ...data, jobs: [] })); const user = userEvent.setup(); await mount(); await screen.findByText(/任务列表不可用/); expect(screen.queryByRole('button', { name: '执行 tiktok.orders' })).toBeNull();
    await user.click(screen.getByRole('button', { name: '刷新任务' })); expect(await screen.findByText('暂无注册任务')).toBeVisible();
  });
  it('treats a mismatched enable acknowledgement as uncertain and retries the captured command only after a usable read', async () => {
    const fetcher = mockApi(async () => json({ job_name: 'tiktok.orders', enabled: false, updated_at: stamp }));
    const user = userEvent.setup(); await mount();
    await user.click(within(await row()).getByRole('button', { name: '启用 tiktok.orders 周期调度' }));
    await screen.findByText(/提交结果未确认/);
    expect(within(await row()).getByRole('button', { name: '启用 tiktok.orders 周期调度' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: '查询核对' }));
    await screen.findByText(/不能证明原命令是否生效/);
    expect(writes(fetcher)).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: '核对后明确重试原操作' }));
    expect(writes(fetcher)).toHaveLength(2);
    expect(JSON.parse(String(writes(fetcher)[1][1].body))).toEqual({ enabled: true });
  });
  it('routes command 401 to the shared session handler without rendering server detail', async () => {
    const unauthorized = vi.fn(); const unset = setUnauthorizedHandler(unauthorized);
    try {
      mockApi(async () => json({ detail: 'TEST_private_session_detail' }, 401));
      const user = userEvent.setup(); await mount();
      await user.click(within(await row('TEST_system')).getByRole('button', { name: '执行 TEST_system' }));
      await screen.findByText(/登录已失效/);
      expect(unauthorized).toHaveBeenCalledTimes(1);
      expect(document.body.textContent).not.toContain('TEST_private_session_detail');
      expect(within(await row('TEST_system')).getByRole('button', { name: '执行 TEST_system' })).toBeDisabled();
    } finally { unset(); }
  });
  it('preserves null and zero status values and server error-existence evidence in the task table', async () => {
    mockApi(undefined, async () => json({ ...data, jobs: [{ ...job(), last_status: { ...job().last_status, last_run_at: null, lag_seconds: 0, cycles_late: null, last_error: 'TEST_job_failure_evidence' } }] }));
    await mount(); const first = await row();
    expect(within(first).getByText('落后周期：—')).toBeVisible();
    expect(within(first).getByText('间隔秒数：0')).toBeVisible();
    expect(within(first).getByText('开始：—')).toBeVisible();
    expect(within(first).queryByText('存在运行错误；详情暂不公开')).toBeVisible();
  });
  it('never renders sensitive persisted upstream error text, while preserving the error-existence fact', async () => {
    const sensitiveError = 'TEST_upstream_body_preview session=TEST_session_secret token=TEST_token_secret';
    mockApi(undefined, async () => json({ ...data, jobs: [{ ...job(), last_status: { ...job().last_status, last_status: 'failed', last_error: sensitiveError } }] }));
    await mount(); const first = await row();
    expect(within(first).getByText('failed')).toBeVisible();
    expect(first.textContent).not.toContain(sensitiveError);
    expect(document.body.textContent).not.toContain('TEST_session_secret');
    expect(document.body.textContent).not.toContain('TEST_token_secret');
    expect(within(first).getByText('存在运行错误；详情暂不公开')).toBeVisible();
  });
  it('keeps the captured unknown command gated when a deferred refresh supersedes verification', async () => {
    let reads = 0; let release!: (value: Response) => void;
    const fetcher = mockApi(async () => json({}, 500), async () => {
      reads += 1;
      return reads === 3 ? new Promise<Response>((resolve) => { release = resolve; }) : json(data);
    });
    const user = userEvent.setup(); await mount();
    await user.click(within(await row('TEST_system')).getByRole('button', { name: '执行 TEST_system' }));
    await screen.findByText(/提交结果未确认/);
    await user.click(screen.getByRole('button', { name: '查询核对' }));
    await screen.findByText(/不能证明原命令是否生效/);
    await user.click(screen.getByRole('button', { name: '刷新任务' }));
    expect(reads).toBe(3);
    // An old enabled retry must not consume recovery if the refresh owns the read guard.
    const staleRetry = screen.queryByRole('button', { name: '核对后明确重试原操作' });
    if (staleRetry) fireEvent.click(staleRetry);
    expect(writes(fetcher)).toHaveLength(1);
    expect(screen.queryByRole('region', { name: '命令核对' })).not.toBeNull();
    expect(screen.getByRole('region', { name: '命令核对' })).toHaveTextContent('TEST_system');
    expect(within(await row('TEST_system')).getByRole('button', { name: '执行 TEST_system' })).toBeDisabled();
    await act(async () => { release(json(data)); });
    expect(screen.queryByRole('button', { name: '核对后明确重试原操作' })).toBeNull();
    expect(within(await row('TEST_system')).getByRole('button', { name: '执行 TEST_system' })).toBeDisabled();
    expect(writes(fetcher)).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: '查询核对' }));
    await screen.findByText(/不能证明原命令是否生效/);
    expect(writes(fetcher)).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: '核对后明确重试原操作' }));
    expect(writes(fetcher)).toHaveLength(2);
    expect(writes(fetcher)[1][0]).toBe('/tts/v2/admin/sync-jobs/TEST_system/trigger');
    expect(JSON.parse(String(writes(fetcher)[1][1].body))).toEqual({});
  });
  it('reconciles a removed selected shop on both refresh and successful verification without a stale-ID POST', async () => {
    for (const viaVerification of [false, true]) {
      let reads = 0; let commands = 0;
      const fetcher = mockApi(async (url, init) => {
        if (viaVerification && ++commands === 1) return json({}, 500);
        const body = JSON.parse(String(init.body));
        return json({ accepted: true, job_name: url.includes('TEST_system') ? 'TEST_system' : 'tiktok.orders', shop_id: body.shop_id ?? null, accepted_at: stamp, message: 'queued' });
      }, async () => json(++reads === 1 ? data : { ...data, tiktok_shops: [] }));
      const user = userEvent.setup(); const view = await mount();
      await user.click(within(await row()).getByRole('button', { name: /执行店铺/ }));
      await user.click(screen.getByRole('option', { name: /TEST_长店铺/ }));
      if (viaVerification) {
        await user.click(within(await row('TEST_system')).getByRole('button', { name: '执行 TEST_system' }));
        await screen.findByText(/提交结果未确认/);
        await user.click(screen.getByRole('button', { name: '查询核对' }));
        await screen.findByText(/不能证明原命令是否生效/);
        await user.click(screen.getByRole('button', { name: '核对后明确重试原操作' }));
        await screen.findByText(/已提交后台，尚未确认完成/);
      } else {
        await user.click(screen.getByRole('button', { name: '刷新任务' }));
      }
      const priorWrites = viaVerification ? 2 : 0;
      await user.click(within(await row()).getByRole('button', { name: '执行 tiktok.orders' }));
      expect(writes(fetcher)).toHaveLength(priorWrites);
      expect(screen.queryByRole('dialog')).not.toBeNull();
      expect(screen.getByRole('dialog')).toHaveTextContent(/全部授权店铺.*上游同步/);
      expect(screen.queryByText(/原执行店铺已不可用/)).toBeVisible();
      await user.click(screen.getByRole('button', { name: '确认全店铺执行' }));
      await screen.findByText(/已提交后台，尚未确认完成/);
      expect(writes(fetcher)).toHaveLength(priorWrites + 1);
      expect(JSON.parse(String(writes(fetcher)[priorWrites][1].body))).toEqual({});
      expect(writes(fetcher).some(([, init]) => String(init.body).includes(data.tiktok_shops[0].shop_id))).toBe(false);
      view.unmount();
    }
  });
  it('clears an obsolete refresh error when verification accepts a valid snapshot without clearing command uncertainty', async () => {
    let reads = 0;
    const fetcher = mockApi(async () => json({}, 500), async () => ++reads === 2 ? json({}, 503) : json(data));
    const user = userEvent.setup(); await mount();
    await user.click(within(await row('TEST_system')).getByRole('button', { name: '执行 TEST_system' }));
    await screen.findByText(/提交结果未确认/);
    await user.click(screen.getByRole('button', { name: '刷新任务' }));
    await screen.findByText(/任务列表不可用/);
    await user.click(screen.getByRole('button', { name: '查询核对' }));
    await screen.findByText(/不能证明原命令是否生效/);
    expect(screen.queryByText(/任务列表不可用/)).toBeNull();
    expect(screen.getByText(/提交结果未确认/)).toBeVisible();
    expect(within(await row('TEST_system')).getByRole('button', { name: '执行 TEST_system' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '核对后明确重试原操作' })).toBeEnabled();
    expect(writes(fetcher)).toHaveLength(1);
  });
  it('preserves full Retry-After across refresh; expiry only permits explicit action', async () => {
    const fetcher = mockApi(async () => json({}, 429, { 'Retry-After': '600' })); const user = userEvent.setup(); const view = await mount(); const first = await row('TEST_system'); vi.useFakeTimers();
    fireEvent.click(within(first).getByRole('button', { name: '执行 TEST_system' })); await act(async () => {}); expect(screen.getByRole('button', { name: '刷新任务' })).toBeDisabled(); expect(within(first).getByRole('button', { name: '执行 TEST_system' })).toBeDisabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(599000); }); expect(screen.getByRole('button', { name: '刷新任务' })).toBeDisabled(); await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); expect(screen.getByRole('button', { name: '刷新任务' })).toBeEnabled(); expect(writes(fetcher)).toHaveLength(1); expect(fetcher.mock.calls.filter(([, init]) => init.method === 'GET')).toHaveLength(1); view.unmount();
    void user;
  });
});
