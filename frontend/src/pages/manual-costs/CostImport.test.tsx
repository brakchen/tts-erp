import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import CostImport from './CostImport';

const result = { totalRows: 4, blankCostRows: 1, skippedRows: 1, importRows: 2, importedCount: 1,
  issues: [{ row: 4, spuId: 'TEST_unknown', shopName: 'TEST 店铺', count: 1, reason: '店铺或商品未匹配' }] };
const json = (data: unknown, status = 201) => new Response(JSON.stringify(data), { status });
const props = () => ({ rootPath: '/tts', blocked: false, hasDrafts: false, onBusy: vi.fn(), onFence: vi.fn(), onFailure: vi.fn(), onImported: vi.fn() });
const file = () => new File(['TEST XLSX'], 'TEST_costs.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('imports immediately after choosing a file and displays the backend result with one request', async () => {
  const fetcher = vi.fn().mockResolvedValue(json(result)); vi.stubGlobal('fetch', fetcher);
  const callbacks = props(); render(<CostImport {...callbacks} />); const user = userEvent.setup();
  const source = file(); await user.upload(screen.getByLabelText('采购价 Excel 文件'), source);
  expect(await screen.findByRole('status')).toHaveTextContent('已更新 1 个商品采购价。共 4 行，有效采购记录 2 行，空金额跳过 1 行，其他跳过 1 行。');
  expect(screen.queryByRole('button', { name: /预览|确认导入/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0][0]).toBe('/tts/v2/reporting/purchase-prices/import');
  const init = fetcher.mock.calls[0][1] as RequestInit;
  expect(init.body).toBe(source); expect(new Headers(init.headers).get('X-Requested-With')).toBe('tts-erp');
  expect(callbacks.onImported).toHaveBeenCalledOnce();
  await user.click(screen.getByText('查看跳过原因（1 项）'));
  expect(screen.getByText(/店铺或商品未匹配/)).toBeVisible();
});

it('prevents duplicate uploads while parsing and writing', async () => {
  let finish!: (response: Response) => void;
  const fetcher = vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })); vi.stubGlobal('fetch', fetcher);
  render(<CostImport {...props()} />); const user = userEvent.setup();
  const input = screen.getByLabelText('采购价 Excel 文件'); await user.upload(input, file());
  expect(input).toBeDisabled(); expect(screen.getByRole('status')).toHaveTextContent('正在解析并导入');
  fireEvent.change(input, { target: { files: [file()] } });
  expect(fetcher).toHaveBeenCalledOnce(); finish(json(result));
  expect(await screen.findByText(/已更新 1 个商品采购价/)).toBeVisible();
  expect(input).toBeEnabled();
});

it('reports a rejected file and permits choosing the corrected file without a confirmation step', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(json({ detail: '表头缺少产品ID' }, 422)).mockResolvedValueOnce(json(result));
  vi.stubGlobal('fetch', fetcher); const callbacks = props(); render(<CostImport {...callbacks} />); const user = userEvent.setup();
  const input = screen.getByLabelText('采购价 Excel 文件'); const source = file(); await user.upload(input, source);
  expect(await screen.findByRole('alert')).toHaveTextContent('表头缺少产品ID'); expect(input).toBeEnabled();
  await user.upload(input, source);
  expect(await screen.findByText(/已更新 1 个商品采购价/)).toBeVisible();
  expect(fetcher).toHaveBeenCalledTimes(2); expect(callbacks.onImported).toHaveBeenCalledOnce();
});

it('reports an all-skipped file as a completed import with zero updates', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ ...result, importedCount: 0, importRows: 0, skippedRows: 3 })));
  render(<CostImport {...props()} />); const user = userEvent.setup(); await user.upload(screen.getByLabelText('采购价 Excel 文件'), file());
  expect(await screen.findByRole('status')).toHaveTextContent('处理完成，没有可更新的商品。');
  expect(screen.getByRole('status')).toHaveTextContent('其他跳过 3 行');
});

it.each(['network', 'invalid-success'] as const)('does not replay or report success after an unknown write: %s', async (failure) => {
  const fetcher = failure === 'network' ? vi.fn().mockRejectedValue(new TypeError('TEST offline')) : vi.fn().mockResolvedValue(json({ importedCount: 1 }));
  vi.stubGlobal('fetch', fetcher); const callbacks = props(); render(<CostImport {...callbacks} />); const user = userEvent.setup();
  const input = screen.getByLabelText('采购价 Excel 文件'); await user.upload(input, file());
  expect(await screen.findByRole('alert')).toHaveTextContent('导入结果未确认');
  expect(input).toBeDisabled(); expect(callbacks.onFence).toHaveBeenLastCalledWith(true);
  fireEvent.change(input, { target: { files: [file()] } });
  expect(fetcher).toHaveBeenCalledOnce(); expect(callbacks.onImported).not.toHaveBeenCalled();
});

it('disables uploads without write access or while edited drafts exist', () => {
  const callbacks = props(); const { rerender } = render(<CostImport {...callbacks} blocked />);
  expect(screen.getByLabelText('采购价 Excel 文件')).toBeDisabled();
  rerender(<CostImport {...callbacks} hasDrafts />);
  expect(screen.getByLabelText('采购价 Excel 文件')).toBeDisabled();
  expect(screen.getByText('请先提交或撤销现有草稿，再导入文件。')).toBeVisible();
});
