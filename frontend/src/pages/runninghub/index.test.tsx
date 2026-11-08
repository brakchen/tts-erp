import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';
import RunningHub from './index';

const props: PageProps = { bootstrap: { rootPath: '/tts', pageId: 'runninghub', navigation: [], releaseId: 'TEST', roleCapabilities: { readwrite: true, admin: true } } };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const assetId = '9007199254740993';
const asset = { asset_id: assetId, kind: 'image', filename: 'TEST_服装.png', content_type: 'image/png', size_bytes: 16, status: 'ready', created_at: '2026-11-07T00:00:00Z', tags: [] };
const template = { id: 'TEST_template', humanId: '1', humanName: 'TEST_人物', name: 'A', version: 1,
  clips: [{ assetId: '10', filename: 'TEST_正面.mp4' }, { assetId: '11', filename: 'TEST_侧面.mp4' }, { assetId: '12', filename: 'TEST_转场.mp4' }] };
const planned = { id: 'TEST_batch', mode: 'live', status: 'PLANNED', total: 1, counts: { PLANNED: 1 }, groups: [], costs: {}, unknownCostTasks: 1 };

function api(command: (path: string, init: RequestInit) => Promise<Response> = async () => json({}), readBatches = () => [planned], read?: (path: string) => Response | undefined) {
  return vi.fn(async (path: string, init: RequestInit = {}) => {
    if ((init.method ?? 'GET') !== 'GET') return command(path, init);
    const override = read?.(path);
    if (override) return override;
    if (path.includes('/v2/media/assets?')) return json({ items: [asset], total: 1, limit: 6, offset: 0 });
    if (path.includes('/v2/media/digital-humans?')) return json([]);
    if (path.endsWith('/config')) return json({
      credentialRef: 'env:RUNNINGHUB_STUDIO_API_KEY', workflowId: '2111762608670990337',
      instanceType: 'ultra', concurrency: 3, workflowVersionId: 'TEST_version', credentialConfigured: true,
    });
    if (path.endsWith('/templates')) return json({ items: [template] });
    if (path.includes('/batches?')) return json({ items: readBatches() });
    if (path.endsWith('/batches/' + planned.id)) return json(readBatches()[0]);
    if (path.includes('/jobs?')) return json({ items: [] });
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
    await user.click(screen.getByRole('button', { name: '批次详情 ' + planned.id }));
    const detail = await screen.findByRole('dialog', { name: '批次详情' });
    expect(await within(detail).findByText('待启动 · 共 1 个任务')).toBeVisible();
    expect(within(detail).queryByRole('button', { name: '终止批次' })).toBeNull();
    await user.click(within(detail).getByRole('button', { name: '关闭批次详情' }));
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
      if (path.endsWith('/plan')) return json({ total: 486, fullTotal: 486, groups: 81, paths: ['成果/TEST/A01.mp4'] });
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
    await user.click(await screen.findByRole('checkbox', { name: /TEST_人物 · 模板 A/ }));
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

  it('requires confirmation in batch details before stopping dispatch', async () => {
    const user = userEvent.setup();
    let batch = { ...planned, status: 'ACTIVE' };
    const terminate = vi.fn(async () => {
      batch = { ...batch, status: 'TERMINATING' };
      return json(batch);
    });
    vi.stubGlobal('fetch', api(terminate, () => [batch]));
    render(<RunningHub {...props} />);
    await user.click(await screen.findByRole('button', { name: '批次详情 ' + batch.id }));
    const detail = await screen.findByRole('dialog', { name: '批次详情' });
    await user.click(await within(detail).findByRole('button', { name: '终止批次' }));
    await user.click(within(detail).getByRole('button', { name: '返回' }));
    expect(terminate).not.toHaveBeenCalled();
    await waitFor(() => expect(within(detail).getByRole('button', { name: '终止批次' })).toHaveFocus());
    await user.click(within(detail).getByRole('button', { name: '终止批次' }));
    await user.click(within(detail).getByRole('button', { name: '确认终止' }));
    expect(await within(detail).findByText(/终止中 · 共 1 个任务/)).toBeVisible();
    expect(terminate).toHaveBeenCalledOnce();
    expect(terminate).toHaveBeenCalledWith('/tts/v2/runninghub/batches/' + batch.id + '/terminate', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ confirmTerminate: true }),
    }));
    expect(within(detail).queryByRole('button', { name: '终止批次' })).toBeNull();
  });

  it('previews the first clip and continues the original batch after explicit paid confirmation', async () => {
    const user = userEvent.setup();
    let batch = { ...planned, status: 'FIRST_CLIP_READY', total: 3, previewJobId: 'TEST_first_job',
      counts: { PLANNED: 0, SUCCEEDED: 1, DEFERRED: 2 } };
    const continueBatch = vi.fn(async () => {
      batch = { ...batch, status: 'ACTIVE', counts: { PLANNED: 2, SUCCEEDED: 1, DEFERRED: 0 } };
      return json(batch);
    });
    vi.stubGlobal('fetch', api(continueBatch, () => [batch], (path) => path.endsWith('/artifacts')
      ? json({ items: [{ id: 'TEST_video', kind: 'final', url: 'https://example.test/first.mp4' }] }) : undefined));
    render(<RunningHub {...props} />);
    await user.click(await screen.findByRole('button', { name: '查看首段 ' + batch.id }));
    const detail = await screen.findByRole('dialog', { name: '查看首段' });
    expect(await within(detail).findByLabelText('首段成片', { selector: 'video' })).toHaveAttribute('src', 'https://example.test/first.mp4');
    expect(within(detail).getByRole('button', { name: '打包下载 ZIP' })).toBeDisabled();
    await user.click(within(detail).getByRole('button', { name: '继续生成剩余 2 段' }));
    const confirm = within(detail).getByRole('button', { name: '确认继续生成' });
    expect(confirm).toBeDisabled();
    await user.click(within(detail).getByRole('checkbox', { name: /RunningHub 费用/ }));
    await user.click(confirm);
    expect(continueBatch).toHaveBeenCalledExactlyOnceWith('/tts/v2/runninghub/batches/' + batch.id + '/continue', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ confirmPaid: true }),
    }));
    expect(await screen.findByText('已安排剩余片段继续生成。')).toBeVisible();
    await user.click(within(detail).getByRole('button', { name: '关闭批次详情' }));
    batch = { ...batch, status: 'SUCCEEDED', counts: { PLANNED: 0, SUCCEEDED: 3, DEFERRED: 0 } };
    await user.click(screen.getByRole('button', { name: '刷新状态' }));
    await user.click(await screen.findByRole('button', { name: '归档' }));
    const archive = await screen.findByRole('dialog', { name: '归档预览' });
    expect(await within(archive).findByRole('link', { name: '打包下载 ZIP' }))
      .toHaveAttribute('href', '/tts/v2/runninghub/batches/' + batch.id + '/export');
  });

  it('creates a template from one or more ready clips and saves their chosen order', async () => {
    const user = userEvent.setup();
    const person = { digital_human_id: '1', name: 'TEST_人物', status: 'active', created_at: asset.created_at, asset_ids: ['10', '11'] };
    const empty = { ...person, digital_human_id: '2', name: 'TEST_零段人物', asset_ids: [] };
    const videos = template.clips.slice(0, 2).map((clip) => ({ ...asset, asset_id: clip.assetId, filename: clip.filename, kind: 'video', content_type: 'video/mp4' }));
    const save = vi.fn(async () => json({ ...template, version: 2, clips: [template.clips[1], template.clips[0]] }));
    vi.stubGlobal('fetch', api(save, undefined, (path) => {
      if (path.includes('/digital-humans?')) return json([person, empty]);
      if (path.endsWith('/digital-humans/2?includePackVideos=true')) return json({ ...empty, assets: [] });
      if (path.endsWith('/digital-humans/1?includePackVideos=true')) return json({ ...person, assets: videos });
      return undefined;
    }));
    render(<RunningHub {...props} />);
    await user.click(screen.getByRole('button', { name: '新建模板' }));
    const editor = await screen.findByRole('dialog', { name: '新建人物模板' });
    const picker = within(editor).getByRole('button', { name: / 人物$/ });
    await user.click(picker);
    await user.click(await screen.findByRole('option', { name: empty.name }));
    expect(await within(editor).findByRole('link', { name: '去数字人素材库添加视频' }))
      .toHaveAttribute('href', '/tts/v2/pages/digital-humans');
    expect(within(editor).getByRole('button', { name: '保存模板' })).toBeDisabled();
    await user.click(picker);
    await user.click(await screen.findByRole('option', { name: person.name }));
    await user.click(await within(editor).findByRole('button', { name: '加入 ' + videos[0].filename }));
    expect(within(editor).getByRole('button', { name: '保存 1 段模板' })).toBeEnabled();
    await user.click(within(editor).getByRole('button', { name: '加入 ' + videos[1].filename }));
    await user.click(within(editor).getByRole('button', { name: '上移 ' + videos[1].filename }));
    await user.click(within(editor).getByRole('button', { name: '保存 2 段模板' }));
    expect(save).toHaveBeenCalledExactlyOnceWith('/tts/v2/runninghub/templates', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ humanId: '1', name: 'A', assetIds: ['11', '10'] }),
    }));
  });
});
