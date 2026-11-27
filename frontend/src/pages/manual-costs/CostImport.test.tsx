import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import CostImport from './CostImport';

const preview = {
  fingerprint: 'a'.repeat(64), totalRows: 3, blankCostRows: 1, skippedRows: 0, importRows: 2, readyCount: 1, issues: [],
  items: [{ spuPk: '9007199254740995', spuId: '1730000000000000001', shopPk: '9007199254740993',
    shopName: 'TEST 店铺', title: 'TEST 商品', sampleCount: 2, unitCost: '12.3456', currency: 'CNY', previousCost: '11.0000', previousCurrency: 'CNY' }],
};
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const props = () => ({ rootPath: '/tts', blocked: false, hasDrafts: false, onBusy: vi.fn(), onFence: vi.fn(), onFailure: vi.fn(), onImported: vi.fn() });
const file = () => new File(['TEST XLSX'], 'TEST_costs.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('uploads binary once for preview and confirms only the server average with its fingerprint', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(json(preview)).mockResolvedValueOnce(json({ importedCount: 1 }, 201));
  vi.stubGlobal('fetch', fetcher);
  const callbacks = props(); render(<CostImport {...callbacks} />); const user = userEvent.setup();
  const source = file(); await user.upload(screen.getByLabelText('采购价 Excel 文件'), source);
  expect(screen.queryByRole('button', { name: /确认导入/ })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '上传并预览' }));
  expect(await screen.findByText('12.3456')).toBeVisible();
  expect(screen.getByText('1730000000000000001')).toBeVisible();
  expect(fetcher.mock.calls[0][0]).toBe('/tts/v2/reporting/manual-costs/import/preview');
  const init = fetcher.mock.calls[0][1] as RequestInit;
  expect(init.body).toBe(source); expect(new Headers(init.headers).get('X-Requested-With')).toBe('tts-erp');
  fireEvent.click(screen.getByRole('button', { name: '确认导入 1 个商品' }));
  fireEvent.click(screen.getByRole('button', { name: '确认导入 1 个商品' }));
  expect(await screen.findByText('已更新 1 个商品采购价，保留原成本历史。')).toBeVisible();
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1][0]).toBe('/tts/v2/reporting/manual-costs/import/confirm?fingerprint=' + preview.fingerprint);
  expect(fetcher.mock.calls[1][1].body).toBe(source);
  expect(callbacks.onImported).toHaveBeenCalledOnce();
});

it('clears stale preview when another file is selected or the server rejects confirmation', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(json(preview)).mockResolvedValueOnce(json({ detail: '请重新预览后确认' }, 409));
  vi.stubGlobal('fetch', fetcher); render(<CostImport {...props()} />); const user = userEvent.setup();
  await user.upload(screen.getByLabelText('采购价 Excel 文件'), file());
  await user.click(screen.getByRole('button', { name: '上传并预览' }));
  await user.click(await screen.findByRole('button', { name: '确认导入 1 个商品' }));
  expect(await screen.findByText('请重新预览后确认')).toBeVisible();
  expect(screen.queryByRole('button', { name: /确认导入/ })).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it('does not replay an unknown write and requires a fresh preview', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(json(preview)).mockRejectedValueOnce(new TypeError('TEST offline'));
  vi.stubGlobal('fetch', fetcher); const callbacks = props(); render(<CostImport {...callbacks} />); const user = userEvent.setup();
  await user.upload(screen.getByLabelText('采购价 Excel 文件'), file());
  await user.click(screen.getByRole('button', { name: '上传并预览' }));
  await user.click(await screen.findByRole('button', { name: '确认导入 1 个商品' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('导入结果未确认');
  expect(screen.getByRole('button', { name: '确认导入 1 个商品' })).toBeDisabled();
  expect(callbacks.onFence).toHaveBeenLastCalledWith(true);
  expect(fetcher).toHaveBeenCalledTimes(2);
  fetcher.mockResolvedValueOnce(json(preview));
  await user.click(screen.getByRole('button', { name: '上传并预览' }));
  expect(await screen.findByRole('button', { name: '确认导入 1 个商品' })).toBeEnabled();
  expect(fetcher).toHaveBeenCalledTimes(3);
});

it('disables uploads without write access or while manual drafts exist', () => {
  const callbacks = props(); const { rerender } = render(<CostImport {...callbacks} blocked />);
  expect(screen.getByLabelText('采购价 Excel 文件')).toBeDisabled();
  rerender(<CostImport {...callbacks} hasDrafts />);
  expect(screen.getByLabelText('采购价 Excel 文件')).toBeDisabled();
  expect(screen.getByText('请先提交或撤销现有草稿，再导入文件。')).toBeVisible();
});

it('invalidates preview when a different file is chosen', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(preview))); render(<CostImport {...props()} />);
  const user = userEvent.setup(); await user.upload(screen.getByLabelText('采购价 Excel 文件'), file());
  await user.click(screen.getByRole('button', { name: '上传并预览' }));
  await screen.findByRole('button', { name: '确认导入 1 个商品' });
  await user.upload(screen.getByLabelText('采购价 Excel 文件'), new File(['TEST other'], 'TEST_other.xlsx'));
  expect(screen.queryByRole('button', { name: /确认导入/ })).not.toBeInTheDocument();
});
