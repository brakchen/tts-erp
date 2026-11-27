import { useEffect, useRef, useState } from 'react';
import { Input } from '@heroui/react';
import OffsetPagination from '../../shared/OffsetPagination';
import { ApiError, requestJson } from '../../shared/api/client';
import { withinRequestLifetime } from '../../shared/api/request-lifetime';
import { record } from './contracts';

interface Issue { row: number | null; spuId: string; shopName: string; count: number; reason: string }
interface ImportResult {
  totalRows: number; blankCostRows: number; importRows: number;
  importedCount: number; skippedRows: number; issues: Issue[];
}
function text(value: unknown): string { if (typeof value !== 'string') throw new Error('导入响应格式不兼容'); return value; }
function count(value: unknown): number { if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error('导入计数不可用'); return value; }
function decodeResult(value: unknown): ImportResult {
  const body = record(value);
  if (!Array.isArray(body.issues)) throw new Error('导入响应格式不兼容');
  const result = { totalRows: count(body.totalRows), blankCostRows: count(body.blankCostRows),
    importRows: count(body.importRows), importedCount: count(body.importedCount), skippedRows: count(body.skippedRows),
    issues: body.issues.map((value) => { const issue = record(value); return { row: issue.row === null ? null : count(issue.row),
      spuId: text(issue.spuId), shopName: text(issue.shopName), count: count(issue.count), reason: text(issue.reason) }; }) };
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
  rootPath: string; blocked: boolean; hasDrafts: boolean;
  onBusy: (busy: boolean) => void; onFence: (fenced: boolean) => void;
  onFailure: (error: unknown) => void; onImported: () => void;
}

export default function CostImport({ rootPath, blocked, hasDrafts, onBusy, onFence, onFailure, onImported }: Props) {
  const [result, setResult] = useState<ImportResult>(); const [fileName, setFileName] = useState('');
  const [busy, setBusy] = useState(false); const [notice, setNotice] = useState('');
  const [uncertain, setUncertain] = useState(false); const [issueOffset, setIssueOffset] = useState(0);
  const controller = useRef<AbortController | undefined>(undefined);
  const mounted = useRef(true); const flight = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; controller.current?.abort(); }; }, []);
  async function upload(file: File) {
    if (blocked || hasDrafts || uncertain || flight.current) return;
    setResult(undefined); setNotice(''); setIssueOffset(0); setFileName(file.name);
    if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > 8 * 1024 * 1024) {
      setNotice('请选择不超过 8 MB 的 .xlsx 文件'); return;
    }
    flight.current = true; setBusy(true); onBusy(true);
    const current = new AbortController(); controller.current = current;
    try {
      const { data, status } = await withinRequestLifetime(current, (signal) => requestJson<unknown>(rootPath, '/v2/reporting/purchase-prices/import', {
        method: 'POST', body: file, headers: { 'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }, signal,
      }));
      if (!mounted.current) return;
      if (status !== 201) throw new Error('导入结果未确认');
      setResult(decodeResult(data)); setUncertain(false); onFence(false); onImported();
    } catch (error: unknown) {
      if (!mounted.current) return;
      onFailure(error);
      if (!(error instanceof ApiError && error.status >= 400 && error.status < 500)) {
        setUncertain(true); onFence(true);
        setNotice('导入结果未确认，请刷新页面核对最新采购价后再操作，避免重复上传。');
      } else setNotice(message(error));
    } finally {
      flight.current = false;
      if (mounted.current) { setBusy(false); onBusy(false); }
    }
  }
  return <section className="erp-panel space-y-4" aria-label="文件导入采购价" aria-busy={busy}>
    <div><h2 className="erp-section-heading">从订单包裹文件导入采购价</h2>
      <p>选择 .xlsx 文件后自动导入。必填列：「采购金额(产品-RMB)」「产品ID」；可选列：「店铺名称」。</p>
      <p className="erp-table-meta">空金额跳过，同一商品取平均采购价。按文件匹配店铺，不受上方筛选限制，原采购价保留历史。</p></div>
    <Input type="file" accept=".xlsx" aria-label="采购价 Excel 文件" disabled={blocked || hasDrafts || busy || uncertain} className="min-w-0 max-w-md"
      onChange={(event) => {
        const selected = event.target.files?.[0];
        // Clear only the native selection so a corrected/rejected file can be chosen again.
        event.target.value = '';
        if (selected) void upload(selected);
      }} />
    {hasDrafts && <p role="status">请先提交或撤销现有草稿，再导入文件。</p>}
    {busy && <p role="status" className="break-words">正在解析并导入 {fileName}，请等待结果…</p>}
    {notice && <p role="alert">{notice}</p>}
    {result && <div className="space-y-3">
      <p role="status">{result.importedCount > 0 ? `已更新 ${result.importedCount} 个商品采购价。` : '处理完成，没有可更新的商品。'}共 {result.totalRows} 行，有效采购记录 {result.importRows} 行，空金额跳过 {result.blankCostRows} 行，其他跳过 {result.skippedRows} 行。</p>
      {result.issues.length > 0 && <details><summary>查看跳过原因（{result.issues.length} 项）</summary>
        <ul className="space-y-2">{result.issues.slice(issueOffset, issueOffset + 50).map((issue, index) => <li key={index} className="break-words">{issue.row ? `第 ${issue.row} 行 · ` : ''}{issue.shopName || '无店铺名'} · {issue.spuId || '无产品ID'} · {issue.reason}（{issue.count} 条）</li>)}</ul>
        {result.issues.length > 50 && <OffsetPagination label="跳过原因分页" offset={issueOffset} pageSize={50} total={result.issues.length} disabled={busy} onPage={setIssueOffset} />}
      </details>}
    </div>}
  </section>;
}
