import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';
import RunningHub from './index';

const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'runninghub', navigation: [], releaseId: 'TEST', roleCapabilities: { readwrite: true, admin: true } } };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const assetId = '9007199254740993';
const asset = { asset_id: assetId, kind: 'image', filename: 'TEST_服装.png', content_type: 'image/png', size_bytes: 16, status: 'ready', created_at: '2026-11-07T00:00:00Z', tags: [] };
const template = { id: 'TEST_template', humanId: '1', humanName: 'TEST_人物', name: 'A', clips: [] };
const planned = { id: 'TEST_batch', mode: 'live', status: 'PLANNED', total: 1, counts: { PLANNED: 1 }, groups: [], costs: {}, unknownCostTasks: 1 };

function api(command: (path: string, init: RequestInit) => Promise<Response> = async () => json({}), readBatches = () => [planned]) {
  return vi.fn(async (path: string, init: RequestInit = {}) => {
    if ((init.method ?? 'GET') !== 'GET') return command(path, init);
    if (path.includes('/v2/media/assets?')) return json({ items: [asset], total: 1, limit: 6, offset: 0 });
    if (path.includes('/v2/media/digital-humans?')) return json([]);
    if (path.endsWith('/config')) return json({
      credentialRef: 'env:RUNNINGHUB_STUDIO_API_KEY', workflowId: '2111762608670990337',
      instanceType: 'ultra', concurrency: 3, workflowVersionId: 'TEST_version', credentialConfigured: true,
    });
    if (path.endsWith('/templates')) return json({ items: [template] });
    if (path.includes('/batches?')) return json({ items: readBatches() });
    return json({});
  });
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('RunningHub workbench and write boundaries', () => {
  it('renders readonly progress and configuration facts without mutation controls', async () => {
    const user = userEvent.setup();
    const fetcher = api();
    vi.stubGlobal('fetch', fetcher);
    render(<RunningHub bootstrap={{ ...props.bootstrap, roleCapabilities: { readwrite: false, admin: false } }} />);
    expect(await screen.findByRole('button', { name: '批次详情 ' + planned.id })).toBeVisible();
    expect(screen.getByText('当前为只读权限。')).toBeVisible();
    expect(screen.queryByRole('button', { name: '预览计划' })).toBeNull();
    await user.click(screen.getByRole('button', { name: '工作流配置' }));
    const dialog = await screen.findByRole('dialog', { name: '工作流配置' });
    expect(within(dialog).getByText('2111762608670990337')).toBeVisible();
    expect(within(dialog).queryByRole('button', { name: '保存配置' })).toBeNull();
    expect(fetcher.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
  });

  it('loads saved settings into the modal and keeps an uncertain write pending once', async () => {
    const user = userEvent.setup();
    let reject: (error: Error) => void = () => {};
    const fetcher = api(async () => new Promise<Response>((_, fail) => { reject = fail; }));
    vi.stubGlobal('fetch', fetcher);
    render(<RunningHub {...props} />);
    const config = screen.getByRole('button', { name: '工作流配置' });
    await waitFor(() => expect(config).toBeEnabled());
    await user.click(config);
    const dialog = await screen.findByRole('dialog', { name: '工作流配置' });
    expect(within(dialog).getByLabelText('凭据引用')).toHaveValue('env:RUNNINGHUB_STUDIO_API_KEY');
    const save = within(dialog).getByRole('button', { name: '保存配置' });
    await user.dblClick(save);
    expect(save).toBeDisabled();
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1);
    await act(async () => reject(new Error('TEST_network_interrupted')));
    expect(await within(dialog).findByText(/写操作不会自动重放/)).toBeVisible();
    expect(within(dialog).getByLabelText('凭据引用')).toHaveValue('env:RUNNINGHUB_STUDIO_API_KEY');
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1);
  });

  it('uses the server plan, keeps a failed save ID stable, and only starts a live batch after confirmation', async () => {
    const user = userEvent.setup();
    let saved = false;
    const drafts: { requestId: string; mode: string; single: boolean; images: { assetId: string }[] }[] = [];
    const starts: object[] = [];
    vi.stubGlobal('fetch', api(async (path, init) => {
      if (path.endsWith('/plan')) return json({ total: 486, groups: 81, paths: ['成果/TEST/A01.mp4'] });
      if (path.endsWith('/batches')) {
        drafts.push(JSON.parse(String(init.body)));
        if (drafts.length === 1) return json({}, 503);
        saved = true;
        return json(planned);
      }
      if (path.endsWith('/start')) {
        starts.push(JSON.parse(String(init.body)));
        return json({ ...planned, status: 'ACTIVE' });
      }
      return json({}, 404);
    }, () => saved ? [planned] : []));

    render(<RunningHub {...props} />);
    expect(screen.queryByLabelText('生成模式')).toBeNull();
    expect(screen.getByRole('checkbox', { name: '仅跑首段' })).not.toBeChecked();
    await user.type(screen.getByLabelText('默认款式编号'), 'TEST_style');
    await user.click(await screen.findByRole('checkbox', { name: asset.filename }));
    await user.click(await screen.findByRole('checkbox', { name: 'TEST_人物 · 模板 A' }));
    await user.click(screen.getByRole('button', { name: /预览计划/ }));
    const plan = await screen.findByRole('dialog', { name: '确认生成计划' });
    expect(within(plan).getByText('服务端计划：486 段成片 / 81 组')).toBeVisible();
    await user.click(within(plan).getByRole('button', { name: '保存批次' }));
    expect(await within(plan).findByText(/写操作不会自动重放/)).toBeVisible();
    await user.click(within(plan).getByRole('button', { name: '保存批次' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '确认生成计划' })).toBeNull());
    expect(drafts).toHaveLength(2);
    expect(drafts[1]).toEqual(drafts[0]);
    expect(drafts[1]).toMatchObject({ mode: 'live', single: false, images: [{ assetId }] });
    expect(starts).toEqual([]);

    await user.click(await screen.findByRole('button', { name: /^启动$/ }));
    const pay = await screen.findByRole('dialog', { name: '启动批次' });
    const confirm = within(pay).getByRole('button', { name: '确认启动' });
    expect(confirm).toBeDisabled();
    await user.click(within(pay).getByRole('checkbox', { name: /RunningHub 费用/ }));
    await user.click(confirm);
    await waitFor(() => expect(starts).toEqual([{ confirmPaid: true }]));
  });
});
