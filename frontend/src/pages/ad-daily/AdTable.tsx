import { Fragment, useState } from 'react';
import { Button } from '@heroui/react';
import type { AdRow } from './contracts';
import { displayDecimal, sorts, type Query, type Sort } from './query';
export const columnLabels = ['日期', '店铺', '计划', '商品', '消耗 USD', '归因订单', '归因 GMV USD', '源 ROI', '最近更新'];
export function displayRoi(value: string | null) { return value === null ? '—' : `${displayDecimal(value)}×`; }
function CopyId({ value, kind }: { value: string; kind: string }) {
  const [notice, setNotice] = useState('');
  async function copy() {
    try { await navigator.clipboard.writeText(value); setNotice('已复制'); }
    catch { setNotice('复制失败，请手动选择 ID'); }
  }
  return <div><code className="break-all">{value || '—'}</code> <Button size="sm" variant="ghost" aria-label={`复制${kind} ID ${value}`} isDisabled={!value} onPress={() => { void copy(); }}>复制</Button><span role="status">{notice}</span></div>;
}
function Row({ item }: { item: AdRow }) {
  const [expanded, setExpanded] = useState(false);
  const detailId = `ad-detail-${item.id}`;
  return <Fragment>
    <tr className="border-b align-top">
      <td className="p-3 whitespace-nowrap">{item.day ?? '—'}</td>
      <td className="p-3"><strong>{item.shop_name ?? item.seller_id}</strong><div className="break-all">shop {item.seller_id}</div><div className="break-all">adv {item.advertiser_id}</div></td>
      <td className="p-3"><CopyId value={item.campaign_id} kind="计划" /></td>
      <td className="p-3"><strong>{item.product_title}</strong><CopyId value={item.product_id} kind="商品" /></td>
      <td className="p-3 text-right whitespace-nowrap">{displayDecimal(item.mixed_real_cost)}</td>
      <td className="p-3 text-right">{displayDecimal(item.onsite_roi2_shopping_sku)}</td>
      <td className="p-3 text-right whitespace-nowrap">{displayDecimal(item.onsite_roi2_shopping_value)}</td>
      <td className="p-3 text-right whitespace-nowrap">{displayRoi(item.onsite_mixed_real_roi2_shopping)}</td>
      <td className="p-3">{item.updated_at ?? '—'}</td>
      <td className="p-3"><Button size="sm" variant="secondary" aria-expanded={expanded} aria-controls={detailId} aria-label={`更多详情 ${item.campaign_id}`} onPress={() => setExpanded(!expanded)}>{expanded ? '收起' : '更多'}</Button></td>
    </tr>
    {expanded && <tr><td colSpan={10} className="p-4"><section id={detailId} aria-label={`记录详情 ${item.campaign_id}`} className="space-y-3">
      <dl className="grid gap-2 sm:grid-cols-2"><div><dt>数据接口</dt><dd className="break-all">{item.endpoint}</dd></div><div><dt>记录 ID</dt><dd>{item.id}</dd></div><div><dt>首次写入</dt><dd>{item.created_at ?? '—'}</dd></div><div><dt>最近更新</dt><dd>{item.updated_at ?? '—'}</dd></div></dl>
      <h3 className="font-semibold">额外采集指标</h3><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(item.metrics_extra, null, 2)}</pre>
    </section></td></tr>}
  </Fragment>;
}
export default function AdTable({ items, query, disabled, onSort }: { items: AdRow[]; query: Query; disabled: boolean; onSort: (sort: Sort) => void }) {
  return <div className="max-w-full overflow-x-auto" role="region" aria-label="广告明细表格滚动区域" tabIndex={0}>
    <table aria-label="广告日明细" className="w-full text-sm">
      <thead><tr>{sorts.map((key, index) => <th key={key} scope="col" className="p-3 text-left whitespace-nowrap" aria-sort={query.sort === key ? query.order === 'asc' ? 'ascending' : 'descending' : 'none'}>
        <Button size="sm" variant="ghost" isDisabled={disabled} aria-label={`排序：${columnLabels[index]}`} onPress={() => onSort(key)}>{columnLabels[index]}{query.sort === key ? query.order === 'asc' ? ' ↑' : ' ↓' : ''}</Button>
      </th>)}<th scope="col">详情</th></tr></thead>
      <tbody>{items.map((item) => <Row key={item.id} item={item} />)}</tbody>
    </table>
  </div>;
}
