import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';
import DigitalHumans from '../digital-humans';
import AssetPicker, { emptyPickerDraft } from '../digital-humans/AssetPicker';
import { decodeDigitalHumanDetail } from './contracts';
import MediaAssets from './index';

const bootstrap: PageProps['bootstrap'] = {
  rootPath: '/tts', pageId: 'media-assets', releaseId: 'TEST',
  navigation: [
    { id: 'media-assets', href: '/tts/v2/pages/media-assets', group: 'TEST', label: '资产管理' },
    { id: 'digital-humans', href: '/tts/v2/pages/digital-humans', group: 'TEST', label: '数字人' },
  ],
  roleCapabilities: { readwrite: true, admin: true },
};
const createdAt = '2026-11-04T00:00:00Z';
const human = {
  digital_human_id: 1, name: 'TEST_数字人', description: 'TEST_现有说明',
  status: 'active', created_at: createdAt, asset_ids: [] as number[],
};
const asset = {
  asset_id: 2, kind: 'image', filename: 'TEST_图片.png', content_type: 'image/png',
  size_bytes: 3, tags: [] as string[], status: 'ready', created_at: createdAt, url: 'https://minio.test/image.png',
};
const page = (items = [asset]) => ({ items, total: items.length, limit: 12, offset: 0, nextCursor: null });
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
function apiFetch(handler: (url: string, init: RequestInit) => Promise<Response>) {
  return vi.fn(async (url: string, init: RequestInit) => {
    if (url.endsWith('/assets/tags')) return json([]);
    const response = await handler(url.replace(/\?includeAssets=false$/, ''), init);
    const parsed = new URL(url, window.location.origin);
    if (parsed.pathname.endsWith('/digital-humans') && parsed.searchParams.has('cursor') && response.ok) {
      const rows = await response.json();
      return json({ items: rows, total: rows.length, nextCursor: null });
    }
    return response;
  });
}
const ticket = {
  asset_id: 2, object_key: 'TEST_a', upload_url: 'https://minio.test/TEST_a',
  upload_expires_at: '2099-01-01T00:00:00Z', required_headers: { 'Content-Type': 'image/png', 'X-Test': 'TEST_signed' },
};
const confirmed = { asset_id: 2, status: 'ready', object_key: 'TEST_a', size_bytes: 3, content_type: 'image/png' };
const assetPath = '/assets/2';
const humanPath = '/digital-humans/1';

class Upload {
  static last: Upload | undefined;
  status = 200;
  upload: { onprogress?: (event: ProgressEvent) => void } = {};
  onload?: () => void;
  open = vi.fn();
  setRequestHeader = vi.fn();
  send() { Upload.last = this; }
  abort() {}
}

beforeEach(() => {
  let nextUrl = 0;
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:TEST_local_' + ++nextUrl) });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
});

afterEach(() => {
  vi.unstubAllGlobals();
  Upload.last = undefined;
  history.replaceState(null, '', '/');
});

const button = (name: string) => screen.getByRole('button', { name });
const findButton = (name: string) => screen.findByRole('button', { name });
const references = () => within(screen.getByRole('list', { name: '关联素材' }));
const findConfirmation = async () => within(await screen.findByRole('alertdialog'));

async function chooseStatus(user: ReturnType<typeof userEvent.setup>, label: string) {
  await user.click(screen.getByRole('button', { name: /状态/ }));
  await user.click(await screen.findByRole('option', { name: label }));
}

function chooseFile() {
  fireEvent.change(screen.getByLabelText('素材文件'), {
    target: { files: [new File(['abc'], asset.filename, { type: 'image/png' })] },
  });
}

async function finishTransfer() {
  await waitFor(() => expect(Upload.last).toBeDefined());
  await act(async () => Upload.last!.onload?.());
}

it('opens the native picker from a visible control and preserves the selected file on an empty selection event', async () => {
  const user = userEvent.setup();
  vi.stubGlobal('XMLHttpRequest', Upload);
  vi.stubGlobal('fetch', apiFetch(async (url) => url.endsWith('/upload-url') ? json(ticket, 201) : json(page())));
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(button('上传素材'));
  const input = screen.getByLabelText('素材文件') as HTMLInputElement;
  expect(input).not.toBeVisible();
  const open = vi.spyOn(input, 'click');
  await user.click(button('上传文件'));
  expect(open).toHaveBeenCalledTimes(1);
  chooseFile();
  const preview = await screen.findByRole('img', { name: '本地图片预览 ' + asset.filename });
  const previewUrl = preview.getAttribute('src');
  await user.clear(screen.getByLabelText('素材名称'));
  await user.type(screen.getByLabelText('素材名称'), 'TEST_只改显示名称');
  await user.click(button('选择标签'));
  await screen.findByText('暂无已有标签。');
  await user.type(screen.getByRole('searchbox', { name: '搜索标签' }), 'TEST_保留标签{Enter}');
  fireEvent.change(input, { target: { files: [] } });
  expect(button('更换文件')).toBeVisible();
  expect(within(screen.getByRole('dialog', { name: '上传素材' })).getByText(asset.filename, { exact: true })).toBeVisible();
  expect(screen.getByLabelText('素材名称')).toHaveValue('TEST_只改显示名称');
  expect(button('移除标签 TEST_保留标签')).toBeVisible();
  expect(preview).toHaveAttribute('src', previewUrl);
  expect(URL.revokeObjectURL).not.toHaveBeenCalled();
  await user.click(button('开始上传'));
  await waitFor(() => expect(Upload.last).toBeDefined());
  expect(button('更换文件')).toBeDisabled();
  expect(input).toBeDisabled();
});

it('requires explicit upload, separates 100% transfer from confirmation, and retries the same asset', async () => {
  const user = userEvent.setup();
  vi.stubGlobal('XMLHttpRequest', Upload);
  let finishConfirm!: (response: Response) => void;
  const firstConfirm = new Promise<Response>((resolve) => { finishConfirm = resolve; });
  let confirms = 0;
  const fetcher = apiFetch(async (url: string) => {
    if (url.endsWith('/upload-url')) return json(ticket, 201);
    if (url.endsWith('/confirm')) return ++confirms === 1 ? firstConfirm : json(confirmed);
    if (url.endsWith(assetPath)) return json(asset);
    const next = new URL(url, location.origin).searchParams.get('cursor') === 'TEST_next';
    return json({ ...page(next ? [{ ...asset, asset_id: 3, filename: 'TEST_续载.png' }] : [asset]), total: 2, nextCursor: next ? null : 'TEST_next' });
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.type(screen.getByLabelText('搜索素材'), 'TEST_filter');
  await user.click(button('搜索'));
  await user.click(await findButton('加载更多'));
  expect(await screen.findByRole('heading', { name: 'TEST_续载.png' })).toBeVisible();
  await user.click(button('上传素材'));
  chooseFile();
  expect(fetcher.mock.calls.some(([url]) => url.endsWith('/upload-url'))).toBe(false);
  await user.click(button('开始上传'));
  await waitFor(() => expect(Upload.last).toBeDefined());
  await act(async () => Upload.last!.upload.onprogress?.(new ProgressEvent('progress', { lengthComputable: true, loaded: 3, total: 3 })));
  expect(screen.getByText(/等待存储响应/)).toBeVisible();
  expect(screen.queryByText(/已入库「/)).not.toBeInTheDocument();
  expect(Upload.last!.setRequestHeader).toHaveBeenCalledWith('X-Test', 'TEST_signed');

  await finishTransfer();
  expect(await screen.findByText('传输完成，正在确认入库')).toBeVisible();
  expect(screen.getByRole('progressbar', { name: '传输完成' })).toHaveAttribute('aria-valuenow', '100');
  expect(screen.queryByText(/已入库「/)).not.toBeInTheDocument();
  finishConfirm(json({}, 503));
  await user.click(await findButton('重试入库'));
  expect(await screen.findByText('已入库「' + asset.filename + '」。')).toBeVisible();
  expect(screen.getByLabelText('搜索素材')).toHaveValue('TEST_filter');
  await user.click(button('查看刚上传的素材'));
  expect(await screen.findByRole('img', { name: '图片预览 ' + asset.filename })).toBeVisible();
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/upload-url'))).toHaveLength(1);
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/assets/2/confirm'))).toHaveLength(2);
  await user.click(button('关闭'));
  expect(screen.getByLabelText('搜索素材')).toHaveValue('TEST_filter');
  await waitFor(() => {
    const listReads = fetcher.mock.calls.filter(([url]) => url.includes('/assets?'));
    const latest = new URL(listReads.at(-1)![0], location.origin).searchParams;
    expect(latest.get('keyword')).toBe('TEST_filter');
    expect(latest.get('cursor')).toBe('');
  });
});

it('retries a failed association without creating or uploading another asset', async () => {
  const user = userEvent.setup();
  vi.stubGlobal('XMLHttpRequest', Upload);
  let linked = false;
  let attempts = 0;
  const fetcher = apiFetch(async (url: string, init: RequestInit) => {
    if (url.endsWith('/upload-url')) return json(ticket, 201);
    if (url.endsWith('/confirm')) return json(confirmed);
    if (url.endsWith(humanPath + '/assets')) {
      expect(JSON.parse(String(init.body))).toEqual({ assetId: '2' });
      if (++attempts === 1) return json({}, 403);
      linked = true;
      return json({ ...human, asset_ids: [2] });
    }
    if (url.endsWith(humanPath)) return json({ ...human, asset_ids: linked ? [2] : [], assets: linked ? [asset] : [] });
    if (url.includes('/assets?')) {
      const associated = new URL(url, window.location.origin).searchParams.has('digitalHumanId');
      return json(page(associated ? (linked ? [asset] : []) : linked ? [] : [asset]));
    }
    return json([{ ...human, asset_ids: linked ? [2] : [] }]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<DigitalHumans bootstrap={bootstrap} />);
  await user.click(await findButton('＋ 添加素材'));
  await user.click(await findButton('上传新素材'));
  chooseFile();
  await user.click(button('开始上传'));
  await waitFor(() => expect(Upload.last!.setRequestHeader).toHaveBeenCalledWith('X-Test', 'TEST_signed'));
  await finishTransfer();
  expect(await screen.findByText(/尚未确认添加到/)).toBeVisible();
  expect(screen.getByRole('link', { name: '在新标签页查看已入库资产' })).toHaveAttribute('href', '/tts/v2/pages/media-assets?asset=2');
  await user.click(button('重试添加'));
  expect(await screen.findByText(/已入库并添加/)).toBeVisible();
  expect(screen.getByRole('heading', { name: '为「' + human.name + '」添加素材' })).toBeVisible();
  await waitFor(() => expect(button('取消')).toBeEnabled());
  await user.click(button('取消'));
  await user.click(await findButton('管理素材 · 1'));
  expect(await references().findByText(asset.filename)).toBeVisible();
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/upload-url'))).toHaveLength(1);
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/confirm'))).toHaveLength(1);
  expect(attempts).toBe(2);
});

it('keeps selections across searches and retries only the remaining asset after a partial submission', async () => {
  const user = userEvent.setup();
  const second = { ...asset, asset_id: 3, filename: 'TEST_第二张.png' };
  const writes: string[] = [];
  const linkedIds: number[] = [];
  const fetcher = apiFetch(async (url: string, init: RequestInit) => {
    if (url.endsWith(humanPath + '/assets')) {
      const { assetId } = JSON.parse(String(init.body));
      writes.push(assetId);
      if (writes.length === 2) return json({}, 503);
      linkedIds.push(Number(assetId));
      return json({ ...human, asset_ids: [...linkedIds] });
    }
    if (url.includes('/assets?')) {
      const params = new URL(url, window.location.origin).searchParams;
      if (!params.has('digitalHumanId')) expect(params.get('status')).toBe('ready');
      expect(params.get('excludeDigitalHumanId') === '1' || params.get('excludeDigitalHumanId') === null).toBe(true);
      const candidates = [asset, second].filter((row) => !params.get('keyword') || row.filename.includes(params.get('keyword')!));
      return json(page(candidates.filter((row) => params.has('digitalHumanId') ? linkedIds.includes(row.asset_id)
        : !params.has('excludeDigitalHumanId') || !linkedIds.includes(row.asset_id))));
    }
    if (url.endsWith(humanPath)) return json({ ...human, asset_ids: [...linkedIds], assets: [asset, second].filter((row) => linkedIds.includes(row.asset_id)) });
    return json([{ ...human, asset_ids: [...linkedIds] }]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<DigitalHumans bootstrap={bootstrap} />);
  await user.click(await findButton('＋ 添加素材'));
  expect(button('确认添加（0）')).toBeDisabled();
  const card = (await screen.findByRole('checkbox', { name: asset.filename })).closest('li');
  expect(card).not.toBeNull();
  const thumbnails = within(card!).getAllByRole('img', { name: '图片预览 ' + asset.filename })
    .filter((image) => image.closest('details') === null);
  expect(thumbnails).toHaveLength(1);
  for (const thumbnail of thumbnails) expect(thumbnail).toBeVisible();
  const details = card!.querySelector('details');
  expect(details).not.toBeNull();
  expect(details).not.toHaveAttribute('open');
  await user.click(within(details!).getByText('查看预览', { selector: 'summary' }));
  expect(details).toHaveAttribute('open');
  expect(within(details!).getByRole('img', { name: '图片预览 ' + asset.filename })).toBeVisible();
  await user.click(screen.getByRole('checkbox', { name: asset.filename }));
  await user.type(screen.getByLabelText('搜索素材'), second.filename);
  await user.click(button('搜索'));
  await user.click(await screen.findByRole('checkbox', { name: second.filename }));
  expect(writes).toEqual([]);
  await user.click(button('确认添加（2）'));
  expect(await screen.findByText(/待添加的勾选已保留/)).toBeVisible();
  await waitFor(() => expect(button('确认添加（1）')).toBeEnabled());
  expect(screen.getByRole('checkbox', { name: second.filename })).toBeChecked();
  expect(writes).toEqual(['2', '3']);

  await user.clear(screen.getByLabelText('搜索素材'));
  await user.click(button('搜索'));
  await user.click(screen.getByRole('checkbox', { name: '隐藏已选内容' }));
  expect(await screen.findByText('已添加至「' + human.name + '」')).toBeVisible();
  expect(screen.getByRole('checkbox', { name: asset.filename })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: asset.filename })).toBeDisabled();
  expect(screen.queryByLabelText(/资产 ID/)).not.toBeInTheDocument();
  await user.click(button('确认添加（1）'));
  await user.click(await findButton('管理素材 · 2'));
  expect(await references().findByText(asset.filename)).toBeVisible();
  expect(references().getByText(second.filename)).toBeVisible();
  expect(writes).toEqual(['2', '3', '3']);
});

it('creates a persona in a dialog and shows its new card without changing routes', async () => {
  const user = userEvent.setup();
  const created = { ...human, digital_human_id: 3, name: 'TEST_新数字人' };
  let saved = false;
  const fetcher = apiFetch(async (url, init) => {
    if (init.method === 'POST') { saved = true; return json(created, 201); }
    const status = new URL(url, location.origin).searchParams.get('status');
    return json(status === 'archived' ? [] : saved ? [created, human] : [human]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<DigitalHumans bootstrap={bootstrap} />);
  await chooseStatus(user, '已归档');
  await user.click(button('新建数字人'));
  const dialog = await screen.findByRole('dialog', { name: '新建数字人' });
  await user.type(within(dialog).getByLabelText('数字人名称'), created.name);
  await user.click(button('创建档案'));
  expect(await screen.findByText(/档案已创建/)).toBeVisible();
  expect(await screen.findByRole('heading', { name: created.name })).toBeVisible();
  expect(new URLSearchParams(location.search).has('human')).toBe(false);
  expect(fetcher.mock.calls.find(([, init]) => init.method === 'POST')?.[1].body)
    .toBe(JSON.stringify({ name: created.name, description: null }));
});

it('preserves a failed metadata edit and only writes changed fields', async () => {
  const user = userEvent.setup();
  const stored = { ...asset, description: 'TEST_保留说明 ', tags: ['TEST_有,标点'] };
  const used = [{ digital_human_id: 1, name: human.name, status: 'archived' }];
  let writes = 0;
  const fetcher = apiFetch(async (url: string, init: RequestInit) => {
    if (init.method === 'PATCH') {
      expect(JSON.parse(String(init.body))).toEqual({ filename: 'TEST_修改后' });
      return ++writes === 1 ? json({}, 422) : json({ ...stored, filename: 'TEST_修改后' });
    }
    if (url.endsWith(assetPath)) return json({ ...stored, used_by: used });
    return json(page([stored]));
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(await findButton('编辑 ' + asset.filename));
  const field = screen.getByLabelText('素材名称');
  await user.clear(field);
  expect(button('保存资料')).toBeDisabled();
  await user.type(field, 'TEST_修改后');
  await user.click(button('保存资料'));
  expect(await screen.findByText(/保存未完成/)).toBeVisible();
  expect(field).toHaveValue('TEST_修改后');
  await user.click(button('保存资料'));
  expect(await screen.findByText(/素材资料已保存/)).toBeVisible();
  expect(writes).toBe(2);
});

it('asks before discarding asset A and never applies its draft to asset B', async () => {
  const user = userEvent.setup();
  const second = { ...asset, asset_id: 4, filename: 'TEST_第二张.png' };
  const fetcher = apiFetch(async (url: string) => {
    if (url.endsWith(assetPath)) return json(asset);
    if (url.endsWith('/assets/4')) return json(second);
    return json(page([asset, second]));
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(await findButton('编辑 ' + asset.filename));
  await user.type(screen.getByLabelText('素材名称'), 'TEST_草稿');
  await user.click(button('取消'));
  const warning = within(await screen.findByRole('alertdialog'));
  await user.click(warning.getByRole('button', { name: '继续处理' }));
  expect(screen.getByLabelText('素材名称')).toHaveValue(asset.filename + 'TEST_草稿');
  await user.click(button('取消'));
  await user.click(await findButton('确认放弃'));
  await user.click(await findButton('编辑 ' + second.filename));
  expect(screen.getByLabelText('素材名称')).toHaveValue(second.filename);
});

it('blocks deletion with fresh archived references', async () => {
  const user = userEvent.setup();
  let detailReads = 0;
  let finishImpact!: (response: Response) => void;
  const impact = new Promise<Response>((resolve) => { finishImpact = resolve; });
  const fetcher = apiFetch(async (url: string, init: RequestInit) => {
    if (init.method === 'DELETE') throw new Error('referenced assets must not be deleted');
    if (url.endsWith(assetPath)) {
      if (++detailReads === 1) return impact;
      return json({ ...asset, used_by: [{ digital_human_id: 1, name: human.name, status: 'archived' }] });
    }
    return json(page());
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(await findButton('删除 ' + asset.filename));
  const confirmation = await findConfirmation();
  const confirm = confirmation.getByRole('button', { name: '确认删除' });
  expect(confirm).toBeDisabled();
  finishImpact(json({}, 503));
  expect(await confirmation.findByText('素材引用不可用')).toBeVisible();
  expect(confirm).toBeDisabled();
  await user.click(confirmation.getByRole('button', { name: '重试素材引用' }));
  expect(await confirmation.findByText(/请解除关联后再删除/)).toBeVisible();
  expect(confirm).toBeDisabled();
  expect(fetcher.mock.calls.some(([, init]) => init.method === 'DELETE')).toBe(false);
});

it('keeps the detail and refreshes references when a concurrent association rejects deletion', async () => {
  const user = userEvent.setup();
  let blocked = false;
  const fetcher = apiFetch(async (url: string, init: RequestInit) => {
    if (init.method === 'DELETE') { blocked = true; return json({ detail: 'ASSET_IN_USE' }, 409); }
    if (url.endsWith(assetPath)) return json({ ...asset, used_by: blocked ? [{ digital_human_id: 1, name: human.name, status: 'active' }] : [] });
    return json(page());
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(await findButton('删除 ' + asset.filename));
  const confirmation = await findConfirmation();
  await waitFor(() => expect(confirmation.getByRole('button', { name: '确认删除' })).toBeEnabled());
  await user.click(confirmation.getByRole('button', { name: '确认删除' }));
  expect(await confirmation.findByText(/请解除关联后再删除/)).toBeVisible();
  expect(confirmation.getByRole('alert')).toHaveTextContent('此素材仍被数字人引用。请先解除全部引用，再从素材库删除。');
  expect(confirmation.getByRole('button', { name: '确认删除' })).toBeDisabled();
  await user.click(confirmation.getByRole('button', { name: '取消' }));
  expect(screen.getByRole('heading', { name: asset.filename })).toBeVisible();
  expect(screen.queryByText(/已从素材库删除/)).not.toBeInTheDocument();
});

it('distinguishes denied reads from empty results and renews a failed preview', async () => {
  const user = userEvent.setup();
  let reads = 0;
  let detailReads = 0;
  vi.stubGlobal('fetch', apiFetch(async (url: string) => {
    if (url.endsWith(assetPath)) return json({ ...asset, url: 'https://minio.test/preview-' + ++detailReads + '.png' });
    if (++reads === 1) return json({}, 403);
    return json(page());
  }));
  render(<MediaAssets bootstrap={bootstrap} />);
  expect(await screen.findByText('你没有权限查看此内容。')).toBeVisible();
  expect(screen.queryByText('素材库还没有文件。')).not.toBeInTheDocument();
  await user.click(button('重试素材列表'));
  await user.click(await findButton('查看 ' + asset.filename));
  fireEvent.error(await screen.findByRole('img', { name: '图片预览 ' + asset.filename }));
  await user.click(await findButton('重新获取预览'));
  expect(await screen.findByRole('img', { name: '图片预览 ' + asset.filename })).toHaveAttribute('src', 'https://minio.test/preview-2.png');
});

it('preserves A/B, query and type while C is uploaded and associated from the picker', async () => {
  const user = userEvent.setup();
  vi.stubGlobal('XMLHttpRequest', Upload);
  const second = { ...asset, asset_id: 3, filename: 'TEST_B.png' };
  const third = { ...asset, asset_id: 4, filename: 'TEST_C.png' };
  const linked: number[] = [];
  const writes: string[] = [];
  const fetcher = apiFetch(async (url: string, init: RequestInit) => {
    if (url.endsWith('/upload-url')) return json({ ...ticket, asset_id: 4 }, 201);
    if (url.endsWith('/confirm')) return json({ ...confirmed, asset_id: 4 });
    if (url.endsWith(humanPath + '/assets')) {
      const { assetId } = JSON.parse(String(init.body));
      writes.push(assetId); linked.push(Number(assetId));
      return json({ ...human, asset_ids: [...linked] });
    }
    if (url.endsWith(humanPath)) return json({ ...human, asset_ids: [...linked], assets: [asset, second, third].filter((row) => linked.includes(row.asset_id)) });
    if (url.includes('/assets?')) {
      const params = new URL(url, window.location.origin).searchParams;
      if (params.has('digitalHumanId')) return json(page([asset, second, third].filter((row) => linked.includes(row.asset_id))));
      expect(params.get('excludeDigitalHumanId')).toBe('1');
      return json(page([asset, second, third].filter((row) => !linked.includes(row.asset_id))));
    }
    return json([{ ...human, asset_ids: [...linked] }]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<DigitalHumans bootstrap={bootstrap} />);
  await user.click(await findButton('＋ 添加素材'));
  await user.click(await screen.findByRole('checkbox', { name: asset.filename }));
  await user.click(screen.getByRole('checkbox', { name: second.filename }));
  expect(screen.getByRole('checkbox', { name: asset.filename })).toBeVisible();
  expect(writes).toEqual([]);
  await user.type(screen.getByLabelText('搜索素材'), 'TEST_');
  await user.click(button('搜索'));
  await user.click(button('上传新素材'));
  expect(screen.getByRole('dialog', { name: '上传新素材' })).toBeVisible();
  fireEvent.change(screen.getByLabelText('素材文件'), { target: { files: [new File(['abc'], third.filename, { type: 'image/png' })] } });
  await user.click(button('开始上传'));
  await finishTransfer();
  expect(await screen.findByText(/已入库并添加/)).toBeVisible();
  expect(screen.getByLabelText('搜索素材')).toHaveValue('TEST_');
  await waitFor(() => expect(button('确认添加（2）')).toBeEnabled());
  expect(await screen.findByRole('checkbox', { name: asset.filename })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: second.filename })).toBeChecked();
  expect(writes).toEqual(['4']);
  await user.click(button('确认添加（2）'));
  expect(await screen.findByRole('heading', { name: human.name })).toBeVisible();
  expect(writes).toEqual(['4', '2', '3']);
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/upload-url'))).toHaveLength(1);
});

it('uses tag arrays, prevents Enter submitting, and retains the pending tag after failed save', async () => {
  const user = userEvent.setup();
  let patchCount = 0;
  const savedTags = ['TEST_有,标点', 'TEST_待保存'];
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (url.endsWith('/assets/tags')) return json(['TEST_有,标点', 'TEST_复用', 'TEST_a b']);
    if (init.method === 'PATCH') {
      patchCount += 1;
      expect(JSON.parse(String(init.body))).toEqual({ tags: savedTags });
      return patchCount === 1 ? json({}, 422) : json({ ...asset, tags: savedTags });
    }
    if (url.endsWith(assetPath)) return json({ ...asset, used_by: [] });
    return json(page());
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(await findButton('编辑 ' + asset.filename));
  const input = screen.getByLabelText('新增标签');
  expect(input).not.toHaveAttribute('maxlength');
  await user.click(button('添加标签'));
  expect(await screen.findByText('请输入标签名称。')).toBeVisible();
  fireEvent.change(input, { target: { value: '  TEST_a  \t b  ' } });
  await user.click(input);
  await user.keyboard('{Enter}');
  expect(screen.getAllByRole('row', { name: 'TEST_a b' })).toHaveLength(1);
  expect(screen.getByRole('row', { name: 'TEST_a b' })).toHaveAttribute('aria-selected', 'true');
  expect(patchCount).toBe(0);
  await user.type(input, 'test_A B{Enter}');
  expect(screen.getAllByRole('row', { name: 'TEST_a b' })).toHaveLength(1);
  expect(screen.queryByRole('row', { name: 'test_A B' })).not.toBeInTheDocument();
  expect(screen.getByText('已选中现有标签「TEST_a b」。')).toBeVisible();
  expect(screen.getByText('已选 1 个 · 可多选，再次点击取消')).toBeVisible();
  await user.click(screen.getByRole('row', { name: 'TEST_a b' }));
  await user.type(input, ' TEST_有,标点 {Enter}');
  expect(patchCount).toBe(0);
  expect(screen.getByRole('row', { name: 'TEST_有,标点' })).toHaveAttribute('aria-selected', 'true');
  expect(input).toHaveValue('');
  await user.type(input, 'TEST_按钮标签');
  await user.click(button('添加标签'));
  expect(screen.getByRole('row', { name: 'TEST_按钮标签' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('row', { name: 'TEST_有,标点' })).toHaveAttribute('aria-selected', 'true');
  expect(input).toHaveValue('');
  expect(patchCount).toBe(0);
  await user.click(screen.getByRole('row', { name: 'TEST_按钮标签' }));
  await user.type(input, 'TEST_有,标点{Enter}');
  expect(screen.getAllByRole('row', { name: 'TEST_有,标点' })).toHaveLength(1);
  await user.type(input, ' TEST_待保存 ');
  await user.click(button('保存资料'));
  expect(await screen.findByText(/保存未完成/)).toBeVisible();
  expect(input).toHaveValue(' TEST_待保存 ');
  await user.click(button('保存资料'));
  expect(await screen.findByText(/素材资料已保存/)).toBeVisible();
  expect(patchCount).toBe(2);
});

it('filters and explicitly adds upload tags without writing discarded or unselected queries', async () => {
  const user = userEvent.setup();
  vi.stubGlobal('XMLHttpRequest', Upload);
  const canonical = 'TEST_Existing Tag';
  const custom = 'TEST_上传,标签';
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (url.endsWith('/assets/tags')) return json([canonical, 'TEST_无关']);
    if (url.endsWith('/upload-url')) {
      expect(JSON.parse(String(init.body)).tags).toEqual([canonical, custom]);
      return json(ticket, 201);
    }
    if (url.endsWith('/confirm')) return json(confirmed);
    return json(page());
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(button('上传素材'));
  expect(screen.queryByRole('searchbox', { name: '搜索标签' })).not.toBeInTheDocument();
  expect(fetcher.mock.calls.some(([url]) => url.endsWith('/assets/tags'))).toBe(false);
  await user.click(button('选择标签'));
  await screen.findByRole('checkbox', { name: canonical });
  const search = () => screen.getByRole('searchbox', { name: '搜索标签' });
  await user.type(search(), 'TEST_取消候选{Enter}');
  await user.click(button('取消'));
  await user.click(await findButton('确认放弃'));
  expect(fetcher.mock.calls.some(([, init]) => init.method === 'POST')).toBe(false);

  await user.click(button('上传素材'));
  chooseFile();
  await user.click(button('选择标签'));
  await screen.findByRole('checkbox', { name: canonical });
  expect(screen.queryByRole('checkbox', { name: 'TEST_取消候选' })).not.toBeInTheDocument();
  await user.type(search(), '  test_existing   TAG  ');
  expect(screen.queryByRole('checkbox', { name: 'TEST_无关' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /^新建/ })).not.toBeInTheDocument();
  await user.keyboard('{Enter}');
  expect(screen.getByRole('checkbox', { name: canonical })).toBeChecked();
  await user.type(search(), custom);
  await user.click(button('新建「' + custom + '」'));
  expect(button('移除标签 ' + custom)).toBeVisible();
  await user.click(screen.getByRole('checkbox', { name: 'TEST_无关' }));
  await user.click(button('移除标签 TEST_无关'));
  expect(screen.getByRole('checkbox', { name: 'TEST_无关' })).not.toBeChecked();
  await user.type(search(), 'TEST_只是搜索');
  await user.click(button('开始上传'));
  await finishTransfer();
  expect(await screen.findByText(/已入库「/)).toBeVisible();
});

it('preserves a typed space after an existing tag prefix until the complete tag is explicitly added', async () => {
  const user = userEvent.setup();
  const prefix = 'TEST_厨房';
  const complete = prefix + ' 场景';
  let writes = 0;
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (url.endsWith('/assets/tags')) return json([prefix]);
    if (init.method === 'PATCH') {
      writes += 1;
      expect(JSON.parse(String(init.body))).toEqual({ tags: [complete] });
      return json({ ...asset, tags: [complete] });
    }
    if (url.endsWith(assetPath)) return json({ ...asset, used_by: [] });
    return json(page());
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(await findButton('编辑 ' + asset.filename));
  expect(await screen.findByRole('row', { name: prefix })).toBeVisible();
  const input = screen.getByLabelText('新增标签');
  await user.type(input, prefix + ' ');
  expect(input).toHaveValue(prefix + ' ');
  await user.type(input, '场景');
  expect(input).toHaveValue(complete);
  expect(screen.queryByRole('row', { name: complete })).not.toBeInTheDocument();
  expect(writes).toBe(0);
  await user.click(button('添加标签'));
  expect(screen.getByRole('row', { name: complete })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('row', { name: prefix })).not.toHaveAttribute('aria-selected', 'true');
  expect(input).toHaveValue('');
  expect(writes).toBe(0);
  await user.click(button('保存资料'));
  expect(await screen.findByText(/素材资料已保存/)).toBeVisible();
  expect(writes).toBe(1);
});

it('reuses a pending existing tag at save without rewriting its raw input', async () => {
  const user = userEvent.setup();
  const canonical = 'TEST_Existing Tag';
  const raw = '  test_existing   TAG  ';
  let writes = 0;
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (url.endsWith('/assets/tags')) return json([canonical]);
    if (init.method === 'PATCH') {
      writes += 1;
      expect(JSON.parse(String(init.body))).toEqual({ tags: [canonical] });
      return json({ ...asset, tags: [canonical] });
    }
    if (url.endsWith(assetPath)) return json({ ...asset, tags: writes ? [canonical] : [], used_by: [] });
    return json(page());
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(await findButton('编辑 ' + asset.filename));
  const option = await screen.findByRole('row', { name: canonical });
  const input = screen.getByLabelText('新增标签');
  await user.type(input, raw);
  expect(input).toHaveValue(raw);
  expect(option).not.toHaveAttribute('aria-selected', 'true');
  expect(writes).toBe(0);
  await user.click(button('保存资料'));
  expect(await screen.findByText(/素材资料已保存/)).toBeVisible();
  expect(writes).toBe(1);
});

it('waits for delayed tag candidates before saving a pending tag', async () => {
  const user = userEvent.setup();
  const canonical = 'TEST_Existing Tag';
  const raw = '  test_existing   TAG  ';
  let releaseCatalog!: (response: Response) => void;
  const delayedCatalog = new Promise<Response>((resolve) => { releaseCatalog = resolve; });
  let writes = 0;
  const fetcher = vi.fn(async (url: string, init: RequestInit) => {
    if (url.endsWith('/assets/tags')) return delayedCatalog;
    if (init.method === 'PATCH') {
      writes += 1;
      expect(JSON.parse(String(init.body))).toEqual({ tags: [canonical] });
      return json({ ...asset, tags: [canonical] });
    }
    if (url.endsWith(assetPath)) return json({ ...asset, tags: writes ? [canonical] : [], used_by: [] });
    return json(page());
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  const submit = '保存资料';
  await user.click(await findButton('编辑 ' + asset.filename));
  expect(await screen.findByText('正在加载已有标签…')).toBeVisible();
  const input = screen.getByLabelText('新增标签');
  await user.type(input, raw);
  expect(input).toHaveValue(raw);
  expect(button('添加标签')).toBeDisabled();
  expect(button(submit)).toBeDisabled();
  await user.keyboard('{Enter}');
  expect(input).toHaveValue(raw);
  expect(screen.queryByRole('row', { name: canonical })).not.toBeInTheDocument();
  await user.click(button('添加标签'));
  await user.click(button(submit));
  expect(writes).toBe(0);

  await act(async () => releaseCatalog(json([canonical])));
  expect(await screen.findByRole('row', { name: canonical })).toBeVisible();
  expect(input).toHaveValue(raw);
  expect(button('添加标签')).toBeEnabled();
  await waitFor(() => expect(button(submit)).toBeEnabled());
  await user.click(button(submit));
  expect(await screen.findByText(/素材资料已保存/)).toBeVisible();
  expect(writes).toBe(1);
});

it.each([false, true])('waits for the post-upload profile read before a partial add (refresh failure: %s)', async (failRefresh) => {
  const user = userEvent.setup();
  vi.stubGlobal('XMLHttpRequest', Upload);
  const second = { ...asset, asset_id: 3, filename: 'TEST_delayed_B.png' };
  const third = { ...asset, asset_id: 4, filename: 'TEST_delayed_C.png' };
  const linked: number[] = [];
  const writes: string[] = [];
  let detailReads = 0;
  let failedB = false;
  let capturedRefresh: Response | undefined;
  let releaseRefresh!: (response: Response) => void;
  const delayedRefresh = new Promise<Response>((resolve) => { releaseRefresh = resolve; });
  const fetcher = apiFetch(async (url: string, init: RequestInit) => {
    if (url.endsWith('/upload-url')) return json({ ...ticket, asset_id: 4 }, 201);
    if (url.endsWith('/confirm')) return json({ ...confirmed, asset_id: 4 });
    if (url.endsWith(humanPath + '/assets')) {
      const { assetId } = JSON.parse(String(init.body));
      writes.push(assetId);
      if (assetId === '3' && !failedB) { failedB = true; return json({}, 503); }
      linked.push(Number(assetId));
      return json({ ...human, asset_ids: [...linked] });
    }
    if (url.endsWith(humanPath)) {
      const snapshot = json({ ...human, asset_ids: [...linked], assets: [asset, second, third].filter((row) => linked.includes(row.asset_id)) });
      if (++detailReads === 2) { capturedRefresh = snapshot; return delayedRefresh; }
      return snapshot;
    }
    if (url.includes('/assets?')) {
      const associated = new URL(url, window.location.origin).searchParams.has('digitalHumanId');
      return json(page([asset, second, third].filter((row) => associated === linked.includes(row.asset_id))));
    }
    return json([{ ...human, asset_ids: [...linked] }]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<DigitalHumans bootstrap={bootstrap} />);
  await user.click(await findButton('＋ 添加素材'));
  await user.click(await screen.findByRole('checkbox', { name: asset.filename }));
  await user.click(screen.getByRole('checkbox', { name: second.filename }));
  await user.click(button('上传新素材'));
  fireEvent.change(screen.getByLabelText('素材文件'), { target: { files: [new File(['abc'], third.filename, { type: 'image/png' })] } });
  await user.click(button('开始上传'));
  await finishTransfer();
  await waitFor(() => expect(capturedRefresh).toBeDefined());
  expect(await screen.findByText('正在加载目标档案…')).toBeVisible();
  expect(button('确认添加（2）')).toBeDisabled();
  expect(button('上传新素材')).toBeDisabled();
  await user.click(button('确认添加（2）'));
  expect(writes).toEqual(['4']);

  await act(async () => releaseRefresh(failRefresh ? json({}, 503) : capturedRefresh!));
  if (failRefresh) {
    expect(await screen.findByText('目标档案不可用')).toBeVisible();
    expect(button('确认添加（2）')).toBeDisabled();
    expect(writes).toEqual(['4']);
    await user.click(button('重试目标档案'));
  }
  await waitFor(() => expect(button('确认添加（2）')).toBeEnabled());
  await waitFor(() => expect(button('确认添加（2）')).not.toHaveAttribute('aria-disabled', 'true'));
  await user.click(button('确认添加（2）'));
  await waitFor(() => expect(writes).toEqual(['4', '2', '3']));
  expect(await screen.findByText(/待添加的勾选已保留/)).toBeVisible();
  expect(writes).toEqual(['4', '2', '3']);
  await waitFor(() => expect(button('确认添加（1）')).toBeEnabled());
  expect(await screen.findByRole('checkbox', { name: second.filename })).toBeChecked();
  await waitFor(() => expect(screen.queryByRole('checkbox', { name: asset.filename })).not.toBeInTheDocument());
  await user.click(button('确认添加（1）'));
  expect(await screen.findByRole('heading', { name: human.name })).toBeVisible();
  await user.click(await findButton('管理素材 · 3'));
  for (const row of [asset, second, third]) expect(await references().findByText(row.filename)).toBeVisible();
  expect(writes).toEqual(['4', '2', '3', '3']);
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/upload-url'))).toHaveLength(1);
});

it('keeps newly filtered cards when an older cursor request finishes late', async () => {
  const user = userEvent.setup();
  const second = { ...asset, asset_id: 3, filename: 'TEST_filtered.png' };
  let release!: (response: Response) => void;
  const delayed = new Promise<Response>((resolve) => { release = resolve; });
  const fetcher = apiFetch(async (url) => {
    const params = new URL(url, location.origin).searchParams;
    if (params.has('keyword')) return json(page([second]));
    if (params.get('cursor') === 'TEST_next') return delayed;
    return json({ ...page(), nextCursor: 'TEST_next' });
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(await findButton('加载更多'));
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => url.includes('TEST_next'))).toBe(true));
  await user.type(screen.getByLabelText('搜索素材'), 'TEST_filtered');
  await user.click(button('搜索'));
  expect(await screen.findByRole('heading', { name: second.filename })).toBeVisible();
  await act(async () => release(json(page([asset]))));
  expect(screen.queryByRole('heading', { name: asset.filename })).not.toBeInTheDocument();
});

it('starts at the first cursor when returning to a previously extended status filter', async () => {
  const user = userEvent.setup();
  const older = { ...human, digital_human_id: 2, name: 'TEST_更早的档案' };
  const fetcher = vi.fn(async (url: string) => {
    const params = new URL(url, location.origin).searchParams;
    if (params.get('status') === 'archived') return json({ items: [], total: 0, nextCursor: null });
    if (params.get('cursor')) return json({ items: [older], total: 2, nextCursor: null });
    return json({ items: [human], total: 2, nextCursor: 'TEST_next' });
  });
  vi.stubGlobal('fetch', fetcher);
  render(<DigitalHumans bootstrap={bootstrap} />);
  await user.click(await findButton('加载更多'));
  expect(await screen.findByRole('heading', { name: older.name })).toBeVisible();
  await chooseStatus(user, '已归档');
  await screen.findByText('暂无已归档的档案。');
  await chooseStatus(user, '启用中');
  expect(await screen.findByRole('heading', { name: human.name })).toBeVisible();
  expect(screen.queryByRole('heading', { name: older.name })).not.toBeInTheDocument();
  expect(new URL(fetcher.mock.calls.at(-1)![0], location.origin).searchParams.get('cursor')).toBe('');
});

it('confirms archiving before a write and keeps its pending confirmation exclusive', async () => {
  const user = userEvent.setup();
  let stored = { ...human, asset_ids: [2], assets: [asset] };
  let releaseWrite!: (response: Response) => void;
  const delayedWrite = new Promise<Response>((resolve) => { releaseWrite = resolve; });
  const fetcher = apiFetch(async (url: string, init: RequestInit) => {
    if (url.endsWith(humanPath)) {
      if (init.method === 'PATCH') {
        const body = JSON.parse(String(init.body));
        if (body.status === 'archived') return delayedWrite;
        stored = { ...stored, status: 'active' };
      }
      return json(stored);
    }
    const status = new URL(url, window.location.origin).searchParams.get('status');
    return json(!status || stored.status === status ? [stored] : []);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<DigitalHumans bootstrap={bootstrap} />);
  await user.click(await findButton('归档 ' + human.name));
  let confirmation = await findConfirmation();
  expect(confirmation.getByRole('heading', { name: '归档「' + human.name + '」' })).toBeVisible();
  expect(fetcher.mock.calls.some(([, init]) => init.method === 'PATCH')).toBe(false);
  await user.click(confirmation.getByRole('button', { name: '取消' }));
  expect(await screen.findByRole('heading', { name: human.name })).toBeVisible();
  await user.click(button('归档 ' + human.name));
  confirmation = await findConfirmation();
  await user.click(confirmation.getByRole('button', { name: '确认归档' }));
  await waitFor(() => expect(fetcher.mock.calls.filter(([, init]) => init.method === 'PATCH')).toHaveLength(1));
  expect(confirmation.getByRole('button', { name: '取消' })).toBeDisabled();
  fireEvent.click(confirmation.getByRole('button', { name: '确认归档' }));
  expect(screen.queryByRole('button', { name: '编辑 ' + human.name })).not.toBeInTheDocument();
  stored = { ...stored, status: 'archived' };
  await act(async () => releaseWrite(json(stored)));
  expect(await screen.findByText('档案已移到「已归档」。')).toBeVisible();
  await chooseStatus(user, '已归档');
  await user.click(await findButton('恢复 ' + human.name));
  await screen.findByText(/档案已恢复/);
  await chooseStatus(user, '启用中');
  expect(await screen.findByRole('heading', { name: human.name })).toBeVisible();
  expect(await findButton('管理素材 · 1')).toBeVisible();
  expect(fetcher.mock.calls.filter(([, init]) => init.method === 'PATCH').map(([, init]) => JSON.parse(String(init.body)).status))
    .toEqual(['archived', 'active']);
});

it('keeps removal confirmation exclusive while pending and retains its failures', async () => {
  const user = userEvent.setup();
  let attached = true;
  let attempts = 0;
  let releaseRemoval!: (response: Response) => void;
  const removal = new Promise<Response>((resolve) => { releaseRemoval = resolve; });
  const fetcher = apiFetch(async (url: string, init: RequestInit) => {
    if (url.endsWith(humanPath + '/assets/2') && init.method === 'DELETE') {
      if (++attempts === 1) return json({}, 403);
      return removal;
    }
    if (url.endsWith(humanPath)) return json({ ...human, asset_ids: attached ? [2] : [], assets: attached ? [asset] : [] });
    if (url.includes('/assets?')) return json(page(attached ? [asset] : []));
    return json([{ ...human, asset_ids: attached ? [2] : [] }]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<DigitalHumans bootstrap={bootstrap} />);
  await user.click(await findButton('管理素材 · 1'));
  await user.click(await findButton('移除关联 ' + asset.filename));
  const confirmation = await findConfirmation();
  await user.click(confirmation.getByRole('button', { name: '确认移除' }));
  expect(await confirmation.findByRole('alert')).toHaveTextContent('你没有权限执行此操作。');
  expect(screen.queryByRole('button', { name: '归档档案' })).not.toBeInTheDocument();
  await user.click(confirmation.getByRole('button', { name: '确认移除' }));
  await waitFor(() => expect(attempts).toBe(2));
  expect(confirmation.getByRole('button', { name: '取消' })).toBeDisabled();
  expect(confirmation.getByRole('button', { name: '确认移除' })).toHaveAttribute('aria-disabled', 'true');
  expect(screen.queryByRole('button', { name: '编辑档案' })).not.toBeInTheDocument();
  fireEvent.click(confirmation.getByRole('button', { name: '确认移除' }));
  expect(fetcher.mock.calls.some(([, init]) => init.method === 'PATCH')).toBe(false);
  expect(attempts).toBe(2);
  attached = false;
  await act(async () => releaseRemoval(json({})));
  expect(await screen.findByText(/已移除 1 项关联/)).toBeVisible();
  await waitFor(() => expect(references().queryByText(asset.filename)).not.toBeInTheDocument());
  expect(fetcher.mock.calls.some(([, init]) => init.method === 'PATCH')).toBe(false);
});

it('previews the selected local file before upload and releases URLs on replacement and departure', async () => {
  const user = userEvent.setup();
  vi.stubGlobal('XMLHttpRequest', Upload);
  const fetcher = apiFetch(async () => json(page()));
  vi.stubGlobal('fetch', fetcher);
  const view = render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(button('上传素材'));
  const image = new File(['abc'], 'TEST_local.png', { type: 'image/png' });
  fireEvent.change(screen.getByLabelText('素材文件'), { target: { files: [image] } });
  expect(await screen.findByRole('img', { name: '本地图片预览 ' + image.name })).toHaveAttribute('src', 'blob:TEST_local_1');
  expect(URL.createObjectURL).toHaveBeenCalledWith(image);
  expect(Upload.last).toBeUndefined();
  expect(fetcher.mock.calls.some(([, init]) => init.method === 'POST')).toBe(false);

  const video = new File(['video'], 'TEST_local.mp4', { type: 'video/mp4' });
  fireEvent.change(screen.getByLabelText('素材文件'), { target: { files: [video] } });
  const preview = await screen.findByLabelText('本地视频预览 ' + video.name);
  expect(preview).toHaveAttribute('src', 'blob:TEST_local_2');
  expect(preview).toHaveAttribute('controls');
  expect(preview).toHaveAttribute('preload', 'metadata');
  expect(preview).toHaveAttribute('playsinline');
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:TEST_local_1');
  fireEvent.error(preview);
  expect(screen.getByText('暂时无法预览所选文件。你可以继续上传，或重新选择文件。')).toBeVisible();
  expect(button('开始上传')).toBeEnabled();
  expect(Upload.last).toBeUndefined();
  expect(fetcher.mock.calls.some(([, init]) => init.method === 'POST')).toBe(false);
  view.unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:TEST_local_2');
  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
});

it('refreshes available rows and hidden matches when references change identity at the same count', async () => {
  const second = { ...asset, asset_id: 3, filename: 'TEST_other.png' };
  const all = [asset, second];
  let linkedIds = [asset.asset_id];
  const fetcher = apiFetch(async (url: string) => {
    const query = new URL(url, window.location.origin).searchParams;
    const matches = all.filter((row) => !query.get('keyword') || row.filename.includes(query.get('keyword')!));
    return json(page(matches.filter((row) => query.has('digitalHumanId') ? linkedIds.includes(row.asset_id)
      : !query.has('excludeDigitalHumanId') || !linkedIds.includes(row.asset_id))));
  });
  vi.stubGlobal('fetch', fetcher);
  const profile = () => decodeDigitalHumanDetail({
    ...human, asset_ids: linkedIds, assets: all.filter((row) => linkedIds.includes(row.asset_id)),
  });
  const draft = { ...emptyPickerDraft, keyword: asset.filename, query: asset.filename };
  const props = {
    root: '/tts', pending: false, error: '', notice: '', draft, revision: 0, onDraft: vi.fn(),
    onAdd: vi.fn(), onClose: vi.fn(), onUpload: vi.fn(), targetRead: { loading: false, retry: vi.fn() },
  };
  const view = render(<AssetPicker {...props} human={profile()} />);
  expect(await screen.findByText('已隐藏 1 项已添加素材')).toBeVisible();
  expect(await screen.findByText('符合条件的素材均已添加。取消勾选“隐藏已选内容”可查看。')).toBeVisible();
  expect(screen.queryByRole('checkbox', { name: asset.filename })).not.toBeInTheDocument();

  linkedIds = [second.asset_id];
  view.rerender(<AssetPicker {...props} human={profile()} />);
  expect(await screen.findByRole('checkbox', { name: asset.filename })).toBeEnabled();
  expect(screen.getByRole('checkbox', { name: asset.filename })).not.toBeChecked();
  await waitFor(() => expect(screen.queryByText('已隐藏 1 项已添加素材')).not.toBeInTheDocument());

  view.rerender(<AssetPicker {...props} human={profile()}
    draft={{ ...draft, keyword: '', query: '', hideAssociated: false }} />);
  expect(await screen.findByRole('checkbox', { name: second.filename })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: second.filename })).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: asset.filename })).toBeEnabled();
  expect(screen.getByText('已添加至「' + human.name + '」')).toBeVisible();
  expect(fetcher.mock.calls.filter(([url]) => new URL(url, window.location.origin).searchParams.has('digitalHumanId')).length)
    .toBeGreaterThanOrEqual(2);
});
