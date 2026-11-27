import { useEffect, useRef, useState } from 'react';
import { Button, Input, Table } from '@heroui/react';
import OffsetPagination from '../../shared/OffsetPagination';
import { ApiError, requestJson } from '../../shared/api/client';
import { withinRequestLifetime } from '../../shared/api/request-lifetime';
import { identity, record } from './contracts';

interface Item {
  spuPk: string; spuId: string; shopPk: string; shopName: string; title: string | null;
  sampleCount: number; unitCost: string; currency: string;
  previousCost: string | null; previousCurrency: string | null;
}
interface Issue { row: number | null; spuId: string; shopName: string; count: number; reason: string }
interface Preview {
  fingerprint: string; totalRows: number; blankCostRows: number; importRows: number;
  readyCount: number; skippedRows: number; items: Item[]; issues: Issue[];
}
function text(value: unknown): string { if (typeof value !== 'string') throw new Error('导入响应格式不兼容'); return value; }
function count(value: unknown): number { if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error('导入计数不可用'); return value; }
function money(value: unknown): string { const result = text(value); if (!/^\d+(?:\.\d+)?$/.test(result)) throw new Error('导入金额不可用'); return result; }
function decodePreview(value: unknown): Preview {
  const body = record(value);
  if (!Array.isArray(body.items) || !Array.isArray(body.issues) || !/^[a-f0-9]{64}$/.test(text(body.fingerprint))) throw new Error('导入响应格式不兼容');
  const items = body.items.map((value): Item => {
    const item = record(value);
    if (item.currency !== 'CNY') throw new Error('导入币种不兼容');
    return { spuPk: identity(item.spuPk), spuId: text(item.spuId), shopPk: identity(item.shopPk), shopName: text(item.shopName),
      title: item.title === null ? null : text(item.title), sampleCount: count(item.sampleCount), unitCost: money(item.unitCost), currency: 'CNY',
      previousCost: item.previousCost === null ? null : money(item.previousCost), previousCurrency: item.previousCurrency === null ? null : text(item.previousCurrency) };
  });
  if (count(body.readyCount) !== items.length) throw new Error('导入计数不一致');
  return { fingerprint: text(body.fingerprint), totalRows: count(body.totalRows), blankCostRows: count(body.blankCostRows),
    importRows: count(body.importRows), readyCount: items.length, skippedRows: count(body.skippedRows), items,
    issues: body.issues.map((value) => { const issue = record(value); return { row: issue.row === null ? null : count(issue.row),
      spuId: text(issue.spuId), shopName: text(issue.shopName), count: count(issue.count), reason: text(issue.reason) }; }) };
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
  const [file, setFile] = useState<File>(); const [preview, setPreview] = useState<Preview>();
  const [busy, setBusy] = useState<'preview' | 'confirm'>(); const [notice, setNotice] = useState('');
  const [uncertain, setUncertain] = useState(false); const [offset, setOffset] = useState(0);
  const [issueOffset, setIssueOffset] = useState(0);
  const controller = useRef<AbortController | undefined>(undefined);
  const mounted = useRef(true); const flight = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; controller.current?.abort(); }; }, []);
  async function upload(confirm: boolean) {
    if (!file || blocked || hasDrafts || flight.current || (confirm && (!preview?.readyCount || uncertain))) return;
    flight.current = true; setBusy(confirm ? 'confirm' : 'preview'); setNotice(''); onBusy(true);
    const current = new AbortController(); controller.current = current;
    try {
      const path = `/v2/reporting/manual-costs/import/${confirm ? `confirm?fingerprint=${preview!.fingerprint}` : 'preview'}`;
      const { data, status } = await withinRequestLifetime(current, (signal) => requestJson<unknown>(rootPath, path, {
        method: 'POST', body: file, headers: { 'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }, signal,
      }));
      if (!mounted.current) return;
      if (confirm) {
        const result = record(data);
        if (status !== 201 || count(result.importedCount) !== preview!.readyCount) throw new Error('导入结果未确认');
        setNotice(`已更新 ${count(result.importedCount)} 个商品采购价，保留原成本历史。`);
        setPreview(undefined); setUncertain(false); onFence(false); onImported();
      } else {
        setPreview(decodePreview(data)); setOffset(0); setIssueOffset(0); setUncertain(false); onFence(false);
      }
    } catch (error: unknown) {
      if (!mounted.current) return;
      onFailure(error);
      if (confirm && !(error instanceof ApiError && error.status >= 400 && error.status < 500)) {
        setUncertain(true); onFence(true);
        setNotice('导入结果未确认。请核对最近提交记录；重新预览后才能再次确认。重复导入会新增成本历史。');
      } else {
        setNotice(message(error));
        // A rejected or failed preview cannot leave an older confirmation available.
        setPreview(undefined);
      }
    } finally {
      flight.current = false;
      if (mounted.current) { setBusy(undefined); onBusy(false); }
    }
  }
  return <section className="erp-panel space-y-4" aria-label="文件导入采购价">
    <div><h2 className="erp-section-heading">从订单包裹文件导入采购价</h2>
      <p>上传 .xlsx 文件，读取第一个工作表的「采购金额(产品-RMB)」「产品ID」及可选的「店铺名称」。</p>
      <p className="erp-table-meta">空采购金额跳过；同一商品的非空金额取算术平均值，按人民币保留四位小数。有店铺信息时匹配店铺，无店铺信息时仅接受唯一商品。</p></div>
    <div className="flex flex-wrap items-center gap-3">
      <Input type="file" accept=".xlsx" aria-label="采购价 Excel 文件" disabled={blocked || hasDrafts || Boolean(busy)} className="min-w-0 max-w-md"
        onChange={(event) => {
          const selected = event.target.files?.[0]; setPreview(undefined); setFile(undefined); setNotice('');
          if (selected && (!selected.name.toLowerCase().endsWith('.xlsx') || selected.size > 8 * 1024 * 1024)) { setNotice('请选择不超过 8 MB 的 .xlsx 文件'); return; }
          setFile(selected);
        }} />
      <Button variant="secondary" isDisabled={blocked || hasDrafts || !file || Boolean(busy)} onPress={() => { void upload(false); }}>上传并预览</Button>
    </div>
    {hasDrafts && <p role="status">请先提交或撤销现有草稿，再导入文件。</p>}
    {busy && <p role="status">{busy === 'preview' ? '正在读取文件并计算平均采购价…' : '正在导入，请等待结果…'}</p>}
    {notice && <p role={uncertain ? 'alert' : 'status'}>{notice}</p>}
    {preview && <>
      <p role="status">共 {preview.totalRows} 行 · 空金额跳过 {preview.blankCostRows} 行 · 其他跳过 {preview.skippedRows} 行 · 待更新 {preview.readyCount} 个商品（{preview.importRows} 条采购记录）</p>
      <Table variant="secondary"><Table.ScrollContainer className="erp-table-scroll" tabIndex={0} role="region" aria-label="采购价导入预览滚动区域">
        <Table.Content aria-label="采购价导入预览" className="erp-table-content min-w-[48rem]">
          <Table.Header>{['店铺', 'SPU / 商品', '有效采购记录', '原人工价', '平均采购价 CNY'].map((label, index) => <Table.Column key={label} isRowHeader={index === 0} className="erp-table-column">{label}</Table.Column>)}</Table.Header>
          <Table.Body renderEmptyState={() => <div>没有可以导入的商品，请查看跳过原因。</div>}>{preview.items.slice(offset, offset + 50).map((item) => <Table.Row key={item.spuPk} id={item.spuPk}>
            <Table.Cell className="erp-table-cell"><p className="w-40 break-words whitespace-normal">{item.shopName || `店铺 #${item.shopPk}`}</p></Table.Cell>
            <Table.Cell className="erp-table-cell"><p className="break-all">{item.spuId}</p><p className="w-56 break-words whitespace-normal">{item.title ?? '—'}</p></Table.Cell>
            <Table.Cell className="erp-table-cell">{item.sampleCount}</Table.Cell>
            <Table.Cell className="erp-table-cell">{item.previousCost ?? '—'} {item.previousCurrency ?? ''}</Table.Cell>
            <Table.Cell className="erp-table-cell">{item.unitCost}</Table.Cell>
          </Table.Row>)}</Table.Body>
        </Table.Content>
      </Table.ScrollContainer></Table>
      {preview.items.length > 50 && <OffsetPagination label="导入预览分页" offset={offset} pageSize={50} total={preview.readyCount} disabled={Boolean(busy)} onPage={setOffset} />}
      {preview.issues.length > 0 && <details><summary>查看跳过原因（{preview.issues.length} 项）</summary>
        <ul className="space-y-2">{preview.issues.slice(issueOffset, issueOffset + 50).map((issue, index) => <li key={index} className="break-words">{issue.row ? `第 ${issue.row} 行 · ` : ''}{issue.shopName || '无店铺名'} · {issue.spuId || '无产品ID'} · {issue.reason}（{issue.count} 条）</li>)}</ul>
        {preview.issues.length > 50 && <OffsetPagination label="跳过原因分页" offset={issueOffset} pageSize={50} total={preview.issues.length} disabled={Boolean(busy)} onPage={setIssueOffset} />}
      </details>}
      <div className="flex flex-wrap items-center gap-3"><Button isDisabled={blocked || hasDrafts || Boolean(busy) || uncertain || preview.readyCount === 0} onPress={() => { void upload(true); }}>确认导入 {preview.readyCount} 个商品</Button>
        <p className="erp-table-meta">确认后统一写入人工成本并保留历史。此文件可能包含多个店铺，不受上方当前店铺筛选限制。</p></div>
    </>}
  </section>;
}
