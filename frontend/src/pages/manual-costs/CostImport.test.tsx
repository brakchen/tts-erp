import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Toast, toastQueue } from '@heroui/react';
import { afterEach, expect, it, vi } from 'vitest';
import CostImport from './CostImport';

const result = { totalRows: 4, blankCostRows: 1, skippedRows: 1, importRows: 2, importedCount: 1,
  issues: [{ row: 4, spuId: 'TEST_unknown', shopName: 'TEST 店铺', count: 1, reason: '店铺或商品未匹配' }] };
const json = (data: unknown, status = 201) => new Response(JSON.stringify(data), { status });
const props = () => ({ rootPath: '/tts', blocked: false, onBusy: vi.fn(), onFence: vi.fn(), onFailure: vi.fn(), onImported: vi.fn() });
const file = () => new File(['TEST XLSX'], 'TEST_costs.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });

afterEach(() => { toastQueue.destroy(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('imports immediately after choosing a file and displays the backend result with one request', async () => {
  const fetcher = vi.fn().mockResolvedValue(json(result)); vi.stubGlobal('fetch', fetcher);
  const callbacks = props(); render(<><CostImport {...callbacks} /><Toast.Provider /></>); const user = userEvent.setup();
  const source = file(); await user.upload(screen.getByLabelText('采购价 Excel 文件'), source);
  expect(await screen.findByText('处理成功')).toBeVisible();
  expect(screen.queryByRole('button', { name: /预览|确认导入/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0][0]).toBe('/tts/v2/reporting/purchase-prices/import');
  const init = fetcher.mock.calls[0][1] as RequestInit;
  expect(init.body).toBe(source); expect(new Headers(init.headers).get('X-Requested-With')).toBe('tts-erp');
  expect(callbacks.onImported).toHaveBeenCalledOnce();
  expect(screen.queryByText(/查看跳过原因/)).not.toBeInTheDocument();
  expect(screen.queryByText(/已更新 1 个商品采购价/)).not.toBeInTheDocument();
});

it('prevents duplicate uploads while parsing and writing', async () => {
  let finish!: (response: Response) => void;
  const fetcher = vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })); vi.stubGlobal('fetch', fetcher);
  render(<><CostImport {...props()} /><Toast.Provider /></>); const user = userEvent.setup();
  const input = screen.getByLabelText('采购价 Excel 文件'); await user.upload(input, file());
  expect(input).toBeDisabled(); expect(await screen.findByText('处理中')).toBeVisible();
  fireEvent.change(input, { target: { files: [file()] } });
  expect(fetcher).toHaveBeenCalledOnce(); finish(json(result));
  expect(await screen.findByText('处理成功')).toBeVisible();
  expect(input).toBeEnabled();
});

it('reports a rejected file and permits choosing the corrected file without a confirmation step', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(json({ detail: '表头缺少产品ID' }, 422)).mockResolvedValueOnce(json(result));
  vi.stubGlobal('fetch', fetcher); const callbacks = props(); render(<><CostImport {...callbacks} /><Toast.Provider /></>); const user = userEvent.setup();
  const input = screen.getByLabelText('采购价 Excel 文件'); const source = file(); await user.upload(input, source);
  expect(await screen.findByText('处理失败')).toBeVisible();
  expect(screen.getByText('表头缺少产品ID')).toBeVisible(); expect(input).toBeEnabled();
  await user.upload(input, source);
  expect(await screen.findByText('处理成功')).toBeVisible();
  expect(fetcher).toHaveBeenCalledTimes(2); expect(callbacks.onImported).toHaveBeenCalledOnce();
});

it('reports an all-skipped file as a completed import with zero updates', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ ...result, importedCount: 0, importRows: 0, skippedRows: 3 })));
  render(<><CostImport {...props()} /><Toast.Provider /></>); const user = userEvent.setup(); await user.upload(screen.getByLabelText('采购价 Excel 文件'), file());
  expect(await screen.findByText('处理成功')).toBeVisible();
  expect(screen.getByText('没有可更新的商品')).toBeVisible();
});

it.each(['network', 'invalid-success'] as const)('does not replay or report success after an unknown write: %s', async (failure) => {
  const fetcher = failure === 'network' ? vi.fn().mockRejectedValue(new TypeError('TEST offline')) : vi.fn().mockResolvedValue(json({ importedCount: 1 }));
  vi.stubGlobal('fetch', fetcher); const callbacks = props(); render(<><CostImport {...callbacks} /><Toast.Provider /></>); const user = userEvent.setup();
  const input = screen.getByLabelText('采购价 Excel 文件'); await user.upload(input, file());
  expect(await screen.findByText('处理失败')).toBeVisible();
  expect(screen.getByText(/导入结果未确认/)).toBeVisible();
  expect(input).toBeDisabled(); expect(callbacks.onFence).toHaveBeenLastCalledWith(true);
  fireEvent.change(input, { target: { files: [file()] } });
  expect(fetcher).toHaveBeenCalledOnce(); expect(callbacks.onImported).not.toHaveBeenCalled();
});

it('disables uploads when the page blocks writing', () => {
  const callbacks = props(); render(<><CostImport {...callbacks} blocked /><Toast.Provider /></>);
  expect(screen.getByLabelText('采购价 Excel 文件')).toBeDisabled();
});
