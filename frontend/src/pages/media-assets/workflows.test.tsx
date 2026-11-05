import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import type { PageProps } from '../../shared/contracts';
import DigitalHumans from '../digital-humans';
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
  size_bytes: 3, tags: [], status: 'ready', created_at: createdAt, url: 'https://minio.test/image.png',
};
const page = (items = [asset]) => ({ items, total: items.length, limit: 24, offset: 0 });
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
function apiFetch(handler: (url: string, init: RequestInit) => Promise<Response>) {
  return vi.fn(async (url: string, init: RequestInit) => url.endsWith('/assets/tags') ? json([]) : handler(url, init));
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

afterEach(() => {
  vi.unstubAllGlobals();
  Upload.last = undefined;
  history.replaceState(null, '', '/');
});

const button = (name: string) => screen.getByRole('button', { name });
const findButton = (name: string) => screen.findByRole('button', { name });

function chooseFile() {
  fireEvent.change(screen.getByLabelText('选择文件'), {
    target: { files: [new File(['abc'], asset.filename, { type: 'image/png' })] },
  });
}

async function finishTransfer() {
  await waitFor(() => expect(Upload.last).toBeDefined());
  await act(async () => Upload.last!.onload?.());
}

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
    return json({ ...page(), total: 25 });
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.type(screen.getByLabelText('搜索素材'), 'TEST_filter');
  await user.click(button('搜索'));
  await waitFor(() => expect(button('下一页')).toBeEnabled());
  await user.click(button('下一页'));
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => new URL(url, location.origin).searchParams.get('offset') === '24')).toBe(true));
  await user.click(button('上传素材'));
  chooseFile();
  expect(fetcher.mock.calls.some(([url]) => url.endsWith('/upload-url'))).toBe(false);
  await user.click(button('开始上传'));
  await waitFor(() => expect(Upload.last).toBeDefined());
  await act(async () => Upload.last!.upload.onprogress?.(new ProgressEvent('progress', { lengthComputable: true, loaded: 3, total: 3 })));
  expect(screen.getByText(/等待存储响应/)).toBeVisible();
  expect(screen.queryByText(/已入库：/)).not.toBeInTheDocument();
  expect(Upload.last!.setRequestHeader).toHaveBeenCalledWith('X-Test', 'TEST_signed');

  await finishTransfer();
  expect(await screen.findByText('正在确认入库…')).toBeVisible();
  expect(screen.queryByText(/已入库：/)).not.toBeInTheDocument();
  finishConfirm(json({}, 503));
  await user.click(await findButton('重试入库确认'));
  expect(await screen.findByText(/已入库：TEST_图片.png/)).toBeVisible();
  expect(screen.getByText(/已清除筛选并返回第一页/)).toBeVisible();
  expect(await screen.findByRole('img', { name: '图片预览 ' + asset.filename })).toBeVisible();
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/upload-url'))).toHaveLength(1);
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/assets/2/confirm'))).toHaveLength(2);
  await user.click(button('返回素材库'));
  expect(screen.getByLabelText('搜索素材')).toHaveValue('');
  expect(screen.getByText('第 1 页')).toBeVisible();
  await waitFor(() => {
    const listReads = fetcher.mock.calls.filter(([url]) => url.includes('/assets?'));
    const latest = new URL(listReads.at(-1)![0], location.origin).searchParams;
    expect(latest.has('keyword')).toBe(false);
    expect(latest.has('offset')).toBe(false);
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
    return json([human]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<DigitalHumans bootstrap={bootstrap} />);
  await user.click(await findButton('查看档案 ' + human.name));
  await user.click(await findButton('上传新素材'));
  chooseFile();
  await user.click(button('开始上传并添加'));
  await waitFor(() => expect(Upload.last!.setRequestHeader).toHaveBeenCalledWith('X-Test', 'TEST_signed'));
  await finishTransfer();
  expect(await screen.findByText(/尚未确认添加到/)).toBeVisible();
  expect(screen.getByRole('link', { name: '在新标签页查看已入库资产' })).toHaveAttribute('href', '/tts/v2/pages/media-assets?asset=2');
  await user.click(button('重试添加资产'));
  expect(await screen.findByText(/已入库并添加到此档案/)).toBeVisible();
  expect(await screen.findByRole('heading', { name: asset.filename })).toBeVisible();
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
      expect(params.get('status')).toBe('ready');
      expect(params.get('excludeDigitalHumanId') === '1' || params.get('excludeDigitalHumanId') === null).toBe(true);
      const candidates = params.get('keyword') === second.filename ? [second] : [asset];
      return json(page(params.has('excludeDigitalHumanId') ? candidates.filter((row) => !linkedIds.includes(row.asset_id)) : candidates));
    }
    if (url.endsWith(humanPath)) return json({ ...human, assets: [] });
    return json([human]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<DigitalHumans bootstrap={bootstrap} />);
  await user.click(await findButton('查看档案 ' + human.name));
  await user.click(await findButton('选择已有素材'));
  expect(button('确认添加(0)')).toBeDisabled();
  expect(await screen.findByRole('img', { name: '图片预览 ' + asset.filename })).toBeVisible();
  await user.click(screen.getByRole('checkbox', { name: asset.filename }));
  await user.type(screen.getByLabelText('搜索素材'), second.filename);
  await user.click(button('搜索'));
  await user.click(await screen.findByRole('checkbox', { name: second.filename }));
  expect(writes).toEqual([]);
  await user.click(button('确认添加(2)'));
  expect(await screen.findByText(/待添加的勾选已保留/)).toBeVisible();
  expect(screen.getByRole('checkbox', { name: second.filename })).toBeChecked();
  expect(writes).toEqual(['2', '3']);

  await user.clear(screen.getByLabelText('搜索素材'));
  await user.click(button('搜索'));
  await user.click(screen.getByRole('checkbox', { name: '隐藏已选内容' }));
  expect(await screen.findByText('已添加至「' + human.name + '」')).toBeVisible();
  expect(screen.getByRole('checkbox', { name: asset.filename })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: asset.filename })).toBeDisabled();
  expect(screen.queryByLabelText(/资产 ID/)).not.toBeInTheDocument();
  await user.click(button('确认添加(1)'));
  expect(await screen.findByRole('heading', { name: asset.filename })).toBeVisible();
  expect(screen.getByRole('heading', { name: second.filename })).toBeVisible();
  expect(writes).toEqual(['2', '3', '3']);
});

it('opens the returned persona and retains write success if its detail fails to load', async () => {
  const user = userEvent.setup();
  const created = { ...human, digital_human_id: 3, name: 'TEST_新数字人' };
  let detailReads = 0;
  const fetcher = apiFetch(async (url: string, init: RequestInit) => {
    if (init.method === 'POST') return json(created, 201);
    if (url.endsWith('/digital-humans/3')) return ++detailReads === 1 ? json({}, 503) : json({ ...created, assets: [] });
    return json([human]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<DigitalHumans bootstrap={bootstrap} />);
  await user.type(screen.getByLabelText('搜索数字人'), 'TEST_筛选');
  await user.click(button('搜索'));
  await user.click(button('新建数字人'));
  await user.type(screen.getByLabelText('名称'), created.name);
  await user.click(button('创建数字人'));
  const dialog = within(await screen.findByRole('region', { name: '数字人档案' }));
  expect(await dialog.findByText(/已创建数字人：TEST_新数字人/)).toBeVisible();
  expect(await dialog.findByText('数字人档案不可用')).toBeVisible();
  await user.click(dialog.getByRole('button', { name: '重试数字人档案' }));
  expect(await findButton('选择已有素材')).toBeVisible();
  expect(new URLSearchParams(location.search).get('human')).toBe('3');
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
    return json(page([asset]));
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(await findButton('查看 ' + asset.filename));
  expect(await screen.findByRole('link', { name: human.name })).toHaveAttribute('href', '/tts/v2/pages/digital-humans?human=1');
  await user.click(button('编辑资料'));
  const field = screen.getByLabelText('显示名称');
  await user.clear(field);
  expect(button('保存资料')).toBeDisabled();
  await user.type(field, 'TEST_修改后');
  await user.click(button('保存资料'));
  expect(await screen.findByText(/保存未完成/)).toBeVisible();
  expect(field).toHaveValue('TEST_修改后');
  await user.click(button('保存资料'));
  expect(await screen.findByText(/素材资料已保存/)).toBeVisible();
  expect(screen.getByRole('link', { name: human.name })).toBeVisible();
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
  await user.click(await findButton('查看 ' + asset.filename));
  await user.click(await findButton('编辑资料'));
  await user.type(screen.getByLabelText('显示名称'), 'TEST_草稿');
  await user.click(button('返回素材详情'));
  const warning = within(await screen.findByRole('alertdialog'));
  await user.click(warning.getByRole('button', { name: '继续处理' }));
  expect(screen.getByLabelText('显示名称')).toHaveValue(asset.filename + 'TEST_草稿');
  await user.click(button('返回素材详情'));
  await user.click(await findButton('确认放弃'));
  await user.click(button('返回素材库'));
  await user.click(await findButton('查看 ' + second.filename));
  await user.click(await findButton('编辑资料'));
  expect(screen.getByLabelText('显示名称')).toHaveValue(second.filename);
});

it('blocks deletion with fresh archived references and offers their profile links', async () => {
  const user = userEvent.setup();
  let detailReads = 0;
  let finishImpact!: (response: Response) => void;
  const impact = new Promise<Response>((resolve) => { finishImpact = resolve; });
  const fetcher = apiFetch(async (url: string, init: RequestInit) => {
    if (init.method === 'DELETE') throw new Error('referenced assets must not be deleted');
    if (url.endsWith(assetPath)) {
      if (++detailReads === 1) return json({ ...asset, used_by: [] });
      if (detailReads === 2) return impact;
      return json({ ...asset, used_by: [{ digital_human_id: 1, name: human.name, status: 'archived' }] });
    }
    return json(page());
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(await findButton('查看 ' + asset.filename));
  await user.click(await findButton('从素材库删除'));
  const confirmation = within(await screen.findByRole('alertdialog'));
  const confirm = confirmation.getByRole('button', { name: '确认删除' });
  expect(confirm).toBeDisabled();
  finishImpact(json({}, 503));
  expect(await confirmation.findByText('当前引用不可用')).toBeVisible();
  expect(confirm).toBeDisabled();
  await user.click(confirmation.getByRole('button', { name: '重试当前引用' }));
  expect(await confirmation.findByRole('link', { name: human.name })).toHaveAttribute('href', '/tts/v2/pages/digital-humans?human=1');
  expect(await confirmation.findByText(/请先解除全部引用/)).toBeVisible();
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
  await user.click(await findButton('查看 ' + asset.filename));
  await user.click(await findButton('从素材库删除'));
  const confirmation = within(await screen.findByRole('alertdialog'));
  await waitFor(() => expect(confirmation.getByRole('button', { name: '确认删除' })).toBeEnabled());
  await user.click(confirmation.getByRole('button', { name: '确认删除' }));
  expect(await confirmation.findByRole('link', { name: human.name })).toBeVisible();
  expect(confirmation.getByRole('button', { name: '确认删除' })).toBeDisabled();
  expect(screen.getByRole('heading', { name: '素材详情 · ' + asset.filename })).toBeVisible();
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
      expect(params.get('excludeDigitalHumanId')).toBe('1');
      return json(page([asset, second, third].filter((row) => !linked.includes(row.asset_id))));
    }
    return json([human]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<DigitalHumans bootstrap={bootstrap} />);
  await user.click(await findButton('添加素材 ' + human.name));
  await user.click(await screen.findByRole('checkbox', { name: asset.filename }));
  await user.click(screen.getByRole('checkbox', { name: second.filename }));
  expect(screen.getByRole('checkbox', { name: asset.filename })).toBeVisible();
  expect(writes).toEqual([]);
  await user.type(screen.getByLabelText('搜索素材'), 'TEST_');
  await user.click(button('搜索'));
  await user.click(button('上传新素材'));
  expect(screen.queryByLabelText('搜索素材')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('选择文件'), { target: { files: [new File(['abc'], third.filename, { type: 'image/png' })] } });
  await user.click(button('开始上传并添加'));
  await finishTransfer();
  expect(await screen.findByText(/原有 2 项待添加选择已保留/)).toBeVisible();
  expect(screen.getByLabelText('搜索素材')).toHaveValue('TEST_');
  expect(button('确认添加(2)')).toBeEnabled();
  expect(await screen.findByRole('checkbox', { name: asset.filename })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: second.filename })).toBeChecked();
  expect(writes).toEqual(['4']);
  await user.click(button('确认添加(2)'));
  expect(await screen.findByRole('heading', { name: '数字人档案 · ' + human.name })).toBeVisible();
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
  await user.click(await findButton('查看 ' + asset.filename));
  await user.click(button('编辑资料'));
  const input = screen.getByLabelText('新增标签');
  expect(input).not.toHaveAttribute('maxlength');
  fireEvent.change(input, { target: { value: '  TEST_a  \t b  ' } });
  await user.click(input);
  await user.keyboard('{Enter}');
  expect(screen.getAllByRole('row', { name: 'TEST_a b' })).toHaveLength(1);
  expect(screen.getByRole('row', { name: 'TEST_a b' })).toHaveAttribute('aria-selected', 'true');
  expect(patchCount).toBe(0);
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

it('includes an uncommitted upload tag in its ticket and cancelling never writes candidates', async () => {
  const user = userEvent.setup();
  vi.stubGlobal('XMLHttpRequest', Upload);
  const fetcher = apiFetch(async (url: string, init: RequestInit) => {
    if (url.endsWith('/upload-url')) {
      expect(JSON.parse(String(init.body)).tags).toEqual(['TEST_上传,标签']);
      return json(ticket, 201);
    }
    if (url.endsWith('/confirm')) return json(confirmed);
    if (url.endsWith(assetPath)) return json(asset);
    return json(page());
  });
  vi.stubGlobal('fetch', fetcher);
  render(<MediaAssets bootstrap={bootstrap} />);
  await user.click(button('上传素材'));
  await user.type(screen.getByLabelText('新增标签'), 'TEST_取消候选{Enter}');
  await user.click(button('返回素材库'));
  await user.click(await findButton('确认放弃'));
  expect(fetcher.mock.calls.some(([, init]) => init.method === 'POST')).toBe(false);
  await user.click(button('上传素材'));
  expect(screen.queryByRole('row', { name: 'TEST_取消候选' })).not.toBeInTheDocument();
  chooseFile();
  await user.type(screen.getByLabelText('新增标签'), ' TEST_上传,标签 ');
  await user.click(button('开始上传'));
  await waitFor(() => expect(Upload.last).toBeDefined());
  await finishTransfer();
  expect(await screen.findByText(/已入库：/)).toBeVisible();
});
