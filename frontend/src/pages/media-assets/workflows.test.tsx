import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';
import DigitalHumans from '../digital-humans';
import MediaAssets from './index';

const bootstrap: PageProps['bootstrap'] = {
  rootPath: '/tts', pageId: 'media-assets', navigation: [], releaseId: 'TEST',
  roleCapabilities: { readwrite: true, admin: true },
};
const createdAt = '2026-11-04T00:00:00Z';
const human = {
  digital_human_id: 1, name: 'TEST_现有数字人', description: 'TEST_现有说明',
  status: 'active', created_at: createdAt, asset_ids: [],
};
const asset = {
  asset_id: 2, kind: 'image', filename: 'TEST_图片.png', content_type: 'image/png',
  size_bytes: 3, tags: [], status: 'ready', created_at: createdAt,
};
const page = (items = [asset]) => ({ items, total: items.length, limit: 100, offset: 0 });
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });

afterEach(() => vi.unstubAllGlobals());

it('shows a successful upload for the confirm endpoint response and sends the ticket headers', async () => {
  const header = vi.fn();
  class Upload {
    status = 200;
    upload = {};
    onload?: () => void;
    open = vi.fn();
    setRequestHeader = header;
    send() { this.onload?.(); }
  }
  vi.stubGlobal('XMLHttpRequest', Upload);
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/upload-url')) return json({
      asset_id: 2, object_key: 'TEST_a', upload_url: 'http://127.0.0.1/TEST_a',
      upload_expires_at: createdAt, required_headers: { 'Content-Type': 'image/png', 'X-Test': 'TEST_signed' },
    }, 201);
    if (url.endsWith('/confirm')) return json({
      asset_id: 2, status: 'ready', object_key: 'TEST_a', size_bytes: 3, content_type: 'image/png',
    });
    return json(page());
  }));
  render(<MediaAssets bootstrap={bootstrap} />);
  fireEvent.change(screen.getByLabelText('选择文件'), {
    target: { files: [new File(['abc'], asset.filename, { type: 'image/png' })] },
  });
  expect(await screen.findByText(/已入库：TEST_图片.png/)).toBeVisible();
  expect(header).toHaveBeenCalledWith('X-Test', 'TEST_signed');
});

it('keeps the create draft independent and accepts the actual create response', async () => {
  const user = userEvent.setup();
  const created = { ...human, digital_human_id: 3, name: 'TEST_新数字人' };
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (init.method === 'POST') return json(created, 201);
    if (url.endsWith('/digital-humans/1')) return json({ ...human, assets: [] });
    return json([human]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<DigitalHumans bootstrap={bootstrap} />);
  await user.type(screen.getByLabelText('名称'), created.name);
  await user.click(await screen.findByRole('button', { name: '查看 TEST_现有数字人' }));
  const [createName, editName] = await screen.findAllByLabelText('名称');
  expect(createName).toHaveValue(created.name);
  expect(editName).toHaveValue(human.name);
  await user.click(screen.getByRole('button', { name: '创建数字人' }));
  expect(await screen.findByText('已创建数字人：TEST_新数字人')).toBeVisible();
  expect(createName).toHaveValue('');
  expect(editName).toHaveValue(human.name);
  expect(fetcher.mock.calls.find(([, init]) => init.method === 'POST')?.[1].body)
    .toBe(JSON.stringify({ name: created.name, description: null }));
});

it('asks the server for unlinked assets and lets the user choose one', async () => {
  const user = userEvent.setup();
  const fetcher = vi.fn(async (url: string) => {
    if (url.includes('excludeDigitalHumanId=1')) return json(page());
    if (url.includes('/assets?')) return json(page([]));
    if (url.endsWith('/digital-humans/1')) return json({ ...human, assets: [] });
    return json([human]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<DigitalHumans bootstrap={bootstrap} />);
  await user.click(await screen.findByRole('button', { name: '查看 TEST_现有数字人' }));
  await user.click(await screen.findByRole('button', { name: '列出可挂载资产' }));
  await user.selectOptions(await screen.findByLabelText('可挂载资产'), asset.asset_id.toString());
  expect(screen.getByLabelText('挂载资产 ID')).toHaveValue(asset.asset_id.toString());
});

it('hides the previous asset actions while loading a different asset', async () => {
  const user = userEvent.setup();
  const second = { ...asset, asset_id: 4, filename: 'TEST_第二张.png' };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/assets/2')) return json(asset);
    if (url.endsWith('/assets/4')) return new Promise<Response>(() => {});
    return json(page([asset, second]));
  }));
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(await screen.findByRole('button', { name: '查看 TEST_图片.png' }));
  expect(await screen.findByRole('button', { name: '删除资产' })).toBeVisible();
  await user.click(screen.getByRole('button', { name: '查看 TEST_第二张.png' }));
  expect(screen.queryByRole('button', { name: '删除资产' })).not.toBeInTheDocument();
  expect(screen.getByText('加载资产详情中…')).toBeVisible();
});

it('opens the second asset page and returns to page one when filters change', async () => {
  const user = userEvent.setup();
  const laterAsset = { ...asset, asset_id: 102, filename: 'TEST_下一页.png' };
  const fetcher = vi.fn(async (url: string) => {
    const query = new URL(url, 'http://127.0.0.1').searchParams;
    if (query.get('keyword')) return json(page());
    if (query.get('offset') === '100') return json({ ...page([laterAsset]), total: 101, offset: 100 });
    return json({ ...page(), total: 101 });
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(await screen.findByRole('button', { name: '下一页' }));
  expect(await screen.findByRole('button', { name: '查看 TEST_下一页.png' })).toBeVisible();
  expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();
  await user.type(screen.getByLabelText('资产关键字'), 'a');
  expect(await screen.findByRole('button', { name: '查看 TEST_图片.png' })).toBeVisible();
  const query = new URL(fetcher.mock.lastCall![0], 'http://127.0.0.1').searchParams;
  expect(query.get('keyword')).toBe('a');
  expect(query.has('offset')).toBe(false);
});

it('sends one create request while the first submission is pending', async () => {
  const user = userEvent.setup();
  let finishCreate!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => { finishCreate = resolve; });
  let attempts = 0;
  const fetcher = vi.fn(async (_url: string, init: RequestInit) => {
    if (init.method !== 'POST') return json([]);
    return ++attempts === 1 ? pending : json(human, 201);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<DigitalHumans bootstrap={bootstrap} />);
  await user.type(screen.getByLabelText('名称'), 'TEST_duplicate');
  const create = screen.getByRole('button', { name: '创建数字人' });
  await user.click(create);
  await user.click(create);
  expect(fetcher.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(1);
  finishCreate(json({}, 422));
  await screen.findByText(/创建未完成/);
  expect(screen.getByLabelText('名称')).toHaveValue('TEST_duplicate');
  await user.click(screen.getByRole('button', { name: '创建数字人' }));
  expect(await screen.findByText(/已创建数字人/)).toBeVisible();
});

it('keeps the second detail after a save response for the first arrives late', async () => {
  const user = userEvent.setup();
  const second = { ...asset, asset_id: 4, filename: 'TEST_第二张.png' };
  let finishSave!: (response: Response) => void;
  const save = new Promise<Response>((resolve) => { finishSave = resolve; });
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    if (init.method === 'PATCH') return save;
    if (url.endsWith('/assets/2')) return json(asset);
    if (url.endsWith('/assets/4')) return json(second);
    return json(page([asset, second]));
  }));
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(await screen.findByRole('button', { name: '查看 TEST_图片.png' }));
  await user.click(await screen.findByRole('button', { name: '保存' }));
  await user.click(screen.getByRole('button', { name: '查看 TEST_第二张.png' }));
  expect(await screen.findByDisplayValue(second.filename)).toBeVisible();
  finishSave(json(asset));
  await screen.findByText('资产信息已保存。');
  expect(screen.queryByDisplayValue(second.filename)).toBeInTheDocument();
});

it.each([
  { Component: MediaAssets, view: '查看 TEST_图片.png', action: '删除资产', target: '/tts/v2/media/assets/2' },
  { Component: DigitalHumans, view: '查看 TEST_现有数字人', action: '删除数字人', target: '/tts/v2/media/digital-humans/1' },
])('requires confirmation and sends one delete for $action', async ({ Component, view, action, target }) => {
  const user = userEvent.setup();
  let finishDelete!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => { finishDelete = resolve; });
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (init.method === 'DELETE') return pending;
    if (url.endsWith('/assets/2')) return json(asset);
    if (url.endsWith('/digital-humans/1')) return json({ ...human, assets: [] });
    return json(url.includes('/assets?') ? page() : [human]);
  });
  const deletes = () => fetcher.mock.calls.filter(([, init]) => init.method === 'DELETE');
  vi.stubGlobal('fetch', fetcher);
  render(<Component bootstrap={bootstrap} />);
  await user.click(await screen.findByRole('button', { name: view }));
  await user.click(await screen.findByRole('button', { name: action }));
  expect(deletes()).toHaveLength(0);
  await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: '取消' }));
  await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
  expect(deletes()).toHaveLength(0);

  await user.click(screen.getByRole('button', { name: action }));
  const dialog = within(await screen.findByRole('alertdialog'));
  expect(dialog.getByText(view.slice(3))).toBeVisible();
  const confirm = dialog.getByRole('button', { name: '确认删除' });
  await user.click(confirm);
  await user.click(confirm);
  expect(deletes()).toHaveLength(1);
  expect(deletes()[0][0]).toBe(target);
  finishDelete(new Response(null, { status: 204 }));
  expect(await screen.findByText(/已删除/)).toBeVisible();
});

it.each([
  { Component: MediaAssets, view: '查看 TEST_图片.png', field: '文件名', savedNotice: '资产信息已保存。', bodyKey: 'filename' },
  { Component: DigitalHumans, view: '查看 TEST_现有数字人', field: '名称', savedNotice: '数字人信息已保存。', bodyKey: 'name' },
] as const)('keeps a rejected edit and saves it when the user retries $field', async ({
  Component, view, field, savedNotice, bodyKey,
}) => {
  const user = userEvent.setup();
  let writes = 0;
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (init.method === 'PATCH') {
      expect(JSON.parse(String(init.body))[bodyKey]).toBe('TEST_修改后');
      if (++writes === 1) return json({}, 422);
      return json(bodyKey === 'filename' ? { ...asset, filename: 'TEST_修改后' } : { ...human, name: 'TEST_修改后' });
    }
    if (url.endsWith('/assets/2')) return json(asset);
    if (url.endsWith('/digital-humans/1')) return json({ ...human, assets: [] });
    return json(url.includes('/assets?') ? page() : [human]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<Component bootstrap={bootstrap} />);
  await user.click(await screen.findByRole('button', { name: view }));
  const edit = await screen.findByDisplayValue(field === '文件名' ? asset.filename : human.name);
  await user.clear(edit);
  expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
  await user.type(edit, 'TEST_修改后');
  await user.click(screen.getByRole('button', { name: '保存' }));
  await screen.findByText(/保存未完成/);
  expect(edit).toHaveValue('TEST_修改后');
  await user.click(screen.getByRole('button', { name: '保存' }));
  expect(await screen.findByText(savedNotice)).toBeVisible();
  expect(writes).toBe(2);
});

it.each([
  { Component: MediaAssets, view: '查看 TEST_图片.png', list: page(), detail: asset, detailPath: '/assets/2', editValue: asset.filename },
  { Component: DigitalHumans, view: '查看 TEST_现有数字人', list: [human], detail: { ...human, assets: [] }, detailPath: '/digital-humans/1', editValue: human.name },
])('recovers from list and detail read failures for $view', async ({
  Component, view, list, detail, detailPath, editValue,
}) => {
  const user = userEvent.setup();
  let listReads = 0;
  let detailReads = 0;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith(detailPath)) return ++detailReads === 1 ? json({}, 503) : json(detail);
    return ++listReads === 1 ? json({}, 503) : json(list);
  }));
  render(<Component bootstrap={bootstrap} />);
  await user.click(await screen.findByRole('button', { name: '重试列表' }));
  await user.click(await screen.findByRole('button', { name: view }));
  await user.click(await screen.findByRole('button', { name: '重试详情' }));
  expect(await screen.findByDisplayValue(editValue)).toBeVisible();
  expect(listReads).toBe(2);
  expect(detailReads).toBe(2);
});
