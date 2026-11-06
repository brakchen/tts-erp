import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';
import RunningHub from './index';

const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'runninghub', navigation: [], releaseId: 'TEST', roleCapabilities: { readwrite: true, admin: true } } };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const batch = { id: 'TEST_batch', mode: 'live', status: 'PAUSED', total: 1, counts: { SUBMISSION_UNKNOWN: 1 }, groups: [], costs: {}, unknownCostTasks: 1 };
const job = { id: 'TEST_job', batchId: batch.id, status: 'SUBMISSION_UNKNOWN', seed: 123, identity: { originalFilename: 'TEST.png', humanName: 'TEST_person', templateName: 'A', clip: 1 } };

function api(command: (path: string, init: RequestInit) => Promise<Response> = async () => json({})) {
  return vi.fn(async (path: string, init: RequestInit = {}) => {
    if ((init.method ?? 'GET') !== 'GET') return command(path, init);
    if (path.includes('/v2/media/assets?')) return json({ items: [], total: 0, limit: 24, offset: 0 });
    if (path.includes('/v2/media/digital-humans?')) return json([]);
    if (path.endsWith('/config')) return json({ credentialRef: 'env:RUNNINGHUB_API_KEY', workflowId: '2111762608670990337', instanceType: 'plus', concurrency: 5, workflowVersionId: 'TEST_version', credentialConfigured: true });
    if (path.endsWith('/templates')) return json({ items: [] });
    if (path.includes('/batches?')) return json({ items: [batch] });
    if (path.endsWith('/batches/' + batch.id)) return json(batch);
    if (path.includes('/jobs?')) return json({ items: [job] });
    if (path.endsWith('/jobs/' + job.id)) return json({ ...job, attempts: [{ id: 'TEST_attempt', number: 1, taskId: null, cloudStatus: null, reserved: true, receipt: {}, usage: null }] });
    if (path.endsWith('/artifacts')) return json({ items: [] });
    return json({});
  });
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('RunningHub recovery and write boundaries', () => {
  it('renders readonly progress without mutation controls', async () => {
    const fetcher = api(); vi.stubGlobal('fetch', fetcher);
    render(<RunningHub bootstrap={{ ...props.bootstrap, roleCapabilities: { readwrite: false, admin: false } }} />);
    expect(await screen.findByText(/2111762608670990337/)).toBeVisible();
    expect(screen.getByText('当前为只读权限。')).toBeVisible();
    expect(screen.queryByRole('button', { name: '保存批次' })).toBeNull();
    expect(screen.queryByRole('button', { name: '保存配置' })).toBeNull();
    expect(fetcher.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
  });

  it('keeps an uncertain write pending once and displays recovery advice', async () => {
    const user = userEvent.setup();
    let reject: (error: Error) => void = () => {};
    const fetcher = api(async () => new Promise<Response>((_, fail) => { reject = fail; }));
    vi.stubGlobal('fetch', fetcher);
    render(<RunningHub {...props} />);
    await user.click(screen.getByText('管理配置'));
    const save = screen.getByRole('button', { name: '保存配置' });
    await user.dblClick(save);
    expect(save).toBeDisabled();
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1);
    await act(async () => reject(new Error('TEST_network_interrupted')));
    expect(await screen.findByText(/写操作不会自动重放/)).toBeVisible();
    expect(screen.getByLabelText('凭据引用')).toHaveValue('env:RUNNINGHUB_API_KEY');
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1);
  });

  it('keeps planned IDs stable and takes the server preview count', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', api(async (path) => path.endsWith('/plan') ? json({ total: 486, groups: 81, paths: ['成果/TEST/A01.mp4'] }) : json({})));
    render(<RunningHub {...props} />);
    const id = screen.getByText(/请求 ID：/).textContent;
    await user.click(screen.getByRole('button', { name: '预览计划' }));
    expect(await screen.findByText('服务端计划：486 个任务 / 81 组')).toBeVisible();
    expect(screen.getByText(/请求 ID：/).textContent).toBe(id);
    await waitFor(() => expect(screen.getByRole('button', { name: '保存批次' })).toBeEnabled());
  });
});
