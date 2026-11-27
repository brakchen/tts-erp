import { useEffect, useRef, useState } from 'react';
import { Button, toast } from '@heroui/react';
import { ApiError, requestJson } from '../../shared/api/client';
import { withinRequestLifetime } from '../../shared/api/request-lifetime';
import { record } from './contracts';

// File transfer + bounded XLSX work gets its own budget; ordinary writes stay at 30s.
export const IMPORT_DEADLINE_MS = 240_000;
interface ImportResult {
  totalRows: number; blankCostRows: number; importRows: number;
  importedCount: number; skippedRows: number;
}
function count(value: unknown): number { if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error('导入计数不可用'); return value; }
function decodeResult(value: unknown): ImportResult {
  const body = record(value);
  if (!Array.isArray(body.issues)) throw new Error('导入响应格式不兼容');
  const result = { totalRows: count(body.totalRows), blankCostRows: count(body.blankCostRows),
    importRows: count(body.importRows), importedCount: count(body.importedCount), skippedRows: count(body.skippedRows) };
  if (result.totalRows !== result.blankCostRows + result.importRows + result.skippedRows
    || result.importedCount > result.importRows || (result.importedCount === 0 && result.importRows !== 0)) throw new Error('导入计数不一致');
  return result;
}
function message(error: unknown): string {
  if (error instanceof ApiError) {
    if ([409, 413, 422].includes(error.status) && typeof error.detail === 'string') return error.detail;
    if (error.status === 403) return '没有采购价写入权限，请联系管理员';
    if (error.status === 401) return '登录已过期，请重新登录';
    if (error.status === 429) return '请求过于频繁，请等待后重新操作';
  }
  return '请求未完成，请检查网络或联系管理员';
}
interface Props {
  rootPath: string; blocked: boolean;
  onBusy: (busy: boolean) => void; onFence: (fenced: boolean) => void;
  onFailure: (error: unknown) => void; onImported: () => void;
}

export default function CostImport({ rootPath, blocked, onBusy, onFence, onFailure, onImported }: Props) {
  const [busy, setBusy] = useState(false); const [uncertain, setUncertain] = useState(false);
  const [fileName, setFileName] = useState(''); const picker = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | undefined>(undefined);
  const activeToast = useRef<string | undefined>(undefined);
  const mounted = useRef(true); const flight = useRef(false);
  useEffect(() => { mounted.current = true; return () => {
    mounted.current = false; controller.current?.abort();
    if (activeToast.current) toast.close(activeToast.current);
  }; }, []);
  async function upload(file: File) {
    if (blocked || uncertain || flight.current) return;
    setFileName(file.name);
    if (activeToast.current) toast.close(activeToast.current);
    if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > 8 * 1024 * 1024) {
      activeToast.current = toast.danger('处理失败', { description: '请选择不超过 8 MB 的 .xlsx 文件', timeout: 6000 }); return;
    }
    flight.current = true; setBusy(true); onBusy(true);
    const notification = toast('处理中', { isLoading: true, timeout: 0 }); activeToast.current = notification;
    const current = new AbortController(); controller.current = current;
    try {
      const { data, status } = await withinRequestLifetime(current, (signal) => requestJson<unknown>(rootPath, '/v2/reporting/purchase-prices/import', {
        method: 'POST', body: file, headers: { 'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }, signal,
      }), undefined, IMPORT_DEADLINE_MS);
      if (!mounted.current) return;
      if (status !== 201) throw new Error('导入结果未确认');
      const result = decodeResult(data);
      activeToast.current = toast.update(notification, '处理成功', { variant: 'success', isLoading: false, timeout: 4000,
        description: result.importedCount === 0 ? '没有可更新的商品' : undefined });
      setUncertain(false); onFence(false); onImported();
    } catch (error: unknown) {
      if (!mounted.current) return;
      onFailure(error);
      if (!(error instanceof ApiError && error.status >= 400 && error.status < 500)) {
        setUncertain(true); onFence(true);
        activeToast.current = toast.update(notification, '处理失败', { variant: 'danger', isLoading: false, timeout: 0,
          description: '导入结果未确认，请刷新页面核对最新采购价后再操作，避免重复上传。' });
      } else activeToast.current = toast.update(notification, '处理失败', { variant: 'danger', isLoading: false, timeout: 6000, description: message(error) });
    } finally {
      flight.current = false;
      if (mounted.current) { setBusy(false); onBusy(false); }
    }
  }
  return <section className="erp-panel space-y-4" aria-label="文件导入采购价" aria-busy={busy}>
    <div><h2 className="erp-section-heading">导入采购价</h2>
      <p className="erp-table-meta">选择订单包裹 .xlsx 文件，自动匹配店铺并更新采购价。</p></div>
    <div className="flex min-w-0 max-w-full items-center gap-3">
    <input ref={picker} type="file" hidden accept=".xlsx" aria-label="采购价 Excel 文件" disabled={blocked || busy || uncertain}
      onChange={(event) => {
        const selected = event.target.files?.[0];
        // Clear only the native selection so a corrected/rejected file can be chosen again.
        event.target.value = '';
        if (selected) void upload(selected);
      }} />
    <Button type="button" variant="secondary" className="shrink-0" isDisabled={blocked || busy || uncertain} onPress={() => picker.current?.click()}>选择文件</Button>
    <span className="min-w-0 flex-1 truncate text-sm text-muted" title={fileName || undefined}>{fileName || '未选择文件'}</span>
    </div>
  </section>;
}
