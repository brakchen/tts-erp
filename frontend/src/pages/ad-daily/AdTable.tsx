import { useEffect, useRef, useState } from 'react';
import { Button, Table } from '@heroui/react';
import type { AdRow } from './contracts';
import { displayDecimal, sorts, type Query, type Sort } from './query';
export const columnLabels = ['日期', '店铺', '计划', '商品', '消耗 USD', '归因订单', '归因 GMV USD', '源 ROI', '最近更新'];
export function displayRoi(value: string | null) { return value === null ? '—' : `${displayDecimal(value)}×`; }
function AdDetails({ item, close }: { item: AdRow; close: () => void }) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);
  return <section id={`ad-detail-${item.id}`} aria-labelledby={`ad-detail-title-${item.id}`} className="erp-panel space-y-3" onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); close(); } }}>
    <h2 ref={heading} tabIndex={-1} id={`ad-detail-title-${item.id}`}>记录详情 {item.campaign_id}</h2>
    <Button size="sm" variant="secondary" onPress={close}>关闭记录详情 {item.campaign_id}</Button>
    <dl className="grid gap-2 sm:grid-cols-2"><div><dt>数据接口</dt><dd className="break-all">{item.endpoint}</dd></div><div><dt>记录 ID</dt><dd>{item.id}</dd></div><div><dt>首次写入</dt><dd>{item.created_at ?? '—'}</dd></div><div><dt>最近更新</dt><dd>{item.updated_at ?? '—'}</dd></div></dl>
    <h3 className="font-semibold">额外采集指标</h3><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(item.metrics_extra, null, 2)}</pre>
  </section>;
}
function CopyId({ value, kind }: { value: string; kind: string }) {
  const [notice, setNotice] = useState('');
  async function copy() {
    try { await navigator.clipboard.writeText(value); setNotice('已复制'); }
    catch { setNotice('复制失败，请手动选择 ID'); }
  }
  return <div><code className="break-all">{value || '—'}</code> <Button size="sm" variant="ghost" aria-label={`复制${kind} ID ${value}`} isDisabled={!value} onPress={() => { void copy(); }}>复制</Button><span role="status">{notice}</span></div>;
}
export default function AdTable({ items, query, disabled, onSort }: { items: AdRow[]; query: Query; disabled: boolean; onSort: (sort: Sort) => void }) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const triggers = useRef(new Map<string, HTMLButtonElement>());
  useEffect(() => {
    const visible = new Set(items.map((item) => String(item.id)));
    setExpanded((previous) => new Set([...previous].filter((id) => visible.has(id))));
  }, [items]);
  function close(id: string) {
    setExpanded((previous) => { const next = new Set(previous); next.delete(id); return next; });
    triggers.current.get(id)?.focus();
  }
  function toggle(id: string) {
    setExpanded((previous) => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  }
  return <div className="space-y-3">
    <Table variant="secondary" className="erp-table"><Table.ScrollContainer className="erp-table-scroll" role="region" aria-label="广告明细表格滚动区域" tabIndex={0}>
      <Table.Content aria-label="广告日明细" className="w-full text-sm erp-table-content">
        <Table.Header className="erp-table-head">{sorts.map((key, index) => <Table.Column key={key} id={key} isRowHeader={index === 0} className="p-3 text-left whitespace-nowrap erp-table-column" aria-sort={query.sort === key ? query.order === 'asc' ? 'ascending' : 'descending' : 'none'}>
          <Button className="erp-table-action" size="sm" variant="ghost" isDisabled={disabled} aria-label={`排序：${columnLabels[index]}`} onPress={() => onSort(key)}>{columnLabels[index]}{query.sort === key ? query.order === 'asc' ? ' ↑' : ' ↓' : ''}</Button>
        </Table.Column>)}<Table.Column id="detail" className="erp-table-column">详情</Table.Column></Table.Header>
        <Table.Body className="erp-table-body">{items.map((item) => <Table.Row key={item.id} id={String(item.id)} className="border-b align-top erp-table-row">
          <Table.Cell className="p-3 whitespace-nowrap erp-table-cell">{item.day ?? '—'}</Table.Cell>
          <Table.Cell className="p-3 erp-table-cell"><strong>{item.shop_name ?? item.seller_id}</strong><div className="break-all">shop {item.seller_id}</div><div className="break-all">adv {item.advertiser_id}</div></Table.Cell>
          <Table.Cell className="p-3 erp-table-cell" focusMode="child"><CopyId value={item.campaign_id} kind="计划" /></Table.Cell>
          <Table.Cell className="p-3 erp-table-cell" focusMode="child"><strong>{item.product_title}</strong><CopyId value={item.product_id} kind="商品" /></Table.Cell>
          <Table.Cell className="p-3 text-right whitespace-nowrap erp-table-cell erp-table-number">{displayDecimal(item.mixed_real_cost)}</Table.Cell>
          <Table.Cell className="p-3 text-right erp-table-cell erp-table-number">{displayDecimal(item.onsite_roi2_shopping_sku)}</Table.Cell>
          <Table.Cell className="p-3 text-right whitespace-nowrap erp-table-cell erp-table-number">{displayDecimal(item.onsite_roi2_shopping_value)}</Table.Cell>
          <Table.Cell className="p-3 text-right whitespace-nowrap erp-table-cell erp-table-number">{displayRoi(item.onsite_mixed_real_roi2_shopping)}</Table.Cell>
          <Table.Cell className="p-3 erp-table-cell">{item.updated_at ?? '—'}</Table.Cell>
          <Table.Cell className="p-3 erp-table-cell" focusMode="child"><Button className="erp-table-action" ref={(element) => { if (element) triggers.current.set(String(item.id), element); else triggers.current.delete(String(item.id)); }} size="sm" variant="secondary" aria-expanded={expanded.has(String(item.id))} aria-controls={`ad-detail-${item.id}`} aria-label={`更多详情 ${item.campaign_id}`} onPress={() => expanded.has(String(item.id)) ? close(String(item.id)) : toggle(String(item.id))}>{expanded.has(String(item.id)) ? '收起' : '更多'}</Button></Table.Cell>
        </Table.Row>)}</Table.Body>
      </Table.Content>
    </Table.ScrollContainer></Table>
    {/* React Aria rows retain a fixed cell collection; expanded records remain
        adjacent to their table instead of introducing an unsupported colSpan. */}
    {items.filter((item) => expanded.has(String(item.id))).map((item) => <AdDetails key={item.id} item={item} close={() => close(String(item.id))} />)}
  </div>;
}
