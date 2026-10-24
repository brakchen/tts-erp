import { Fragment, useEffect, useRef, useState } from 'react';
import { Button } from '@heroui/react';
import { pathFor } from '../../shared/api/paths';
import type { Comparison, ComparisonItem } from './contracts';
export function displayValue(value: string | number | null): string { return value === null ? '—' : String(value); }
/** 展示值 only, adapted from this page's legacy displayChange; never used for scope, facts or totals. */
export function displayChange(previous: string | null, current: string | null, unit = '') {
  const unavailable = { difference: '—', percent: '—', direction: '不可比较', reason: '缺少可比较原值；请查看两期事实。' };
  if (previous === null || current === null) return unavailable;
  const before = Number(previous), after = Number(current), delta = after - before;
  // Same conservative four-decimal presentation guard as the existing page.
  if ([before, after, delta].some((v) => !Number.isFinite(v) || Math.abs(v) > Number.MAX_SAFE_INTEGER / 10_000)) return { ...unavailable, reason: '数值过大，展示精度不足；请查看两期原值。' };
  const signed = (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${new Intl.NumberFormat('en-US', { maximumFractionDigits: 4, useGrouping: false }).format(Math.abs(v))}`;
  return { difference: `${signed(delta)}${unit ? ` ${unit}` : ''}`, percent: before > 0 ? `${signed(delta / before * 100)}%` : '—', direction: delta > 0 ? '↑ 上升' : delta < 0 ? '↓ 下降' : '→ 持平', reason: '仅上期大于 0 时计算展示变化率。' };
}
function Metric({ previous, current, label, previousReason, currentReason, change = false, unit = '' }: {
  previous: string | number | null; current: string | number | null; label: string; previousReason?: string | null; currentReason?: string | null; change?: boolean; unit?: string;
}) {
  const display = change ? displayChange(previous as string | null, current as string | null, unit) : undefined;
  return <div className="space-y-2"><strong>{label}</strong><div>上期 <span className="tabular-nums break-all">{displayValue(previous)}</span>{previous === null && <p>{previousReason || `上期${label}事实缺失`}</p>}</div>
    <div>本期 <span className="tabular-nums break-all">{displayValue(current)}</span>{current === null && <p>{currentReason || `本期${label}事实缺失`}</p>}</div>
    {display && <div className="text-sm">{display.direction}<p>展示差额 {display.difference}</p><p>展示变化率 {display.percent}</p><p>{display.reason}</p></div>}
  </div>;
}
function Metrics({ item }: { item: ComparisonItem }) {
  return <>
    <Metric label="ROI（比率）" previous={item.previousRoi} current={item.currentRoi} previousReason={item.previousRoiUnavailableReason} currentReason={item.currentRoiUnavailableReason} change />
    <Metric label="净利润（CNY）" previous={item.previousNetProfitCny} current={item.currentNetProfitCny} change unit="CNY" />
    <Metric label="订单数" previous={item.previousOrderCount} current={item.currentOrderCount} />
    <Metric label="广告消耗（CNY）" previous={item.previousSpendCny} current={item.currentSpendCny} />
  </>;
}
function Facts({ item }: { item: ComparisonItem }) {
  return <div className="space-y-2">
    {(['previous', 'current'] as const).map((period) => <Fragment key={period}>{item[`${period}NoOrderAdSpend`] === true && <p><strong>{period === 'current' ? '本期' : '上期'}：无单但有广告费</strong></p>}{item[`${period}NoOrderAdSpend`] === null && <p>{period === 'current' ? '本期' : '上期'}无单投放事实未知，不能以缺失冒充零。</p>}</Fragment>)}
    {item.warnings.map((warning, index) => <p key={index}>{warning}</p>)}
    <p>计算时间：{item.basisCalculatedAt}（不是数据更新日期）</p>
  </div>;
}
function drilldownHref(root: string, item: ComparisonItem): string | undefined {
  const supplied = item.drilldown.pageUrl;
  if (!supplied.startsWith('/v2/pages/spu-roi?')) return;
  try {
    const target = new URL(supplied, 'https://local.invalid');
    if (target.searchParams.get('shop_pk') !== String(item.shopPk) || target.searchParams.get('spu_ids') !== item.spuId
      || target.searchParams.get('w_start') !== item.drilldown.startDate || target.searchParams.get('w_end') !== item.drilldown.endDate) return;
    return pathFor(root, supplied);
  } catch { return; }
}
function DetailLink({ root, item }: { root: string; item: ComparisonItem }) {
  const href = drilldownHref(root, item);
  return href ? <a className="underline" href={href}>本期盈利详情（{item.drilldown.startDate} 至 {item.drilldown.endDate}）</a> : <p>盈利详情链接不可用</p>;
}
function Expanded({ item, meta, root, close }: { item: ComparisonItem; meta: Comparison['meta']; root: string; close: () => void }) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);
  return <section role="region" aria-labelledby={`detail-title-${item.spuPk}`} className="space-y-4 p-3" onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); close(); } }}>
    <h2 ref={heading} tabIndex={-1} id={`detail-title-${item.spuPk}`}>SPU {item.spuId} 对比明细</h2>
    <Button variant="secondary" onPress={close}>关闭 SPU 明细</Button>
    <p>店铺 {item.shopPk} · 内部编号 {item.spuPk} · {item.windowDays} 天 · 截至日 {item.anchorDate}</p>
    <p>本期 {meta.periods.current.startDate} 至 {meta.periods.current.endDate}；上期 {meta.periods.previous.startDate} 至 {meta.periods.previous.endDate}（{meta.reportingTimezone}）</p>
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><Metrics item={item} /></div>
    <Facts item={item} /><DetailLink item={item} root={root} /><p>可通过浏览器返回原查询；估值时点不同不保证两处逐分相同。</p>
  </section>;
}
function Row({ item, meta, root }: { item: ComparisonItem; meta: Comparison['meta']; root: string }) {
  const [expanded, setExpanded] = useState(false); const trigger = useRef<HTMLButtonElement>(null);
  const close = () => { setExpanded(false); trigger.current?.focus(); };
  return <><tr className="border-b align-top">
    <th scope="row" className="p-3 text-left"><div className="min-w-48 space-y-3"><strong className="break-all">{item.spuId}</strong><p>内部编号 {item.spuPk} · 店铺 {item.shopPk}</p>
      <Button ref={trigger} size="sm" variant="secondary" aria-expanded={expanded} aria-controls={`detail-${item.spuPk}`} aria-label={`查看对比明细 ${item.spuId}`} onPress={() => expanded ? close() : setExpanded(true)}>查看对比明细</Button><p><DetailLink item={item} root={root} /></p></div></th>
    <td className="min-w-48 p-3"><Metric label="ROI（比率）" previous={item.previousRoi} current={item.currentRoi} previousReason={item.previousRoiUnavailableReason} currentReason={item.currentRoiUnavailableReason} change /></td>
    <td className="min-w-48 p-3"><Metric label="净利润（CNY）" previous={item.previousNetProfitCny} current={item.currentNetProfitCny} change unit="CNY" /></td>
    <td className="min-w-32 p-3"><Metric label="订单数" previous={item.previousOrderCount} current={item.currentOrderCount} /></td>
    <td className="min-w-48 p-3"><Metric label="广告消耗（CNY）" previous={item.previousSpendCny} current={item.currentSpendCny} /></td>
    <td className="min-w-64 p-3"><Facts item={item} /></td>
  </tr>{expanded && <tr><td colSpan={6} id={`detail-${item.spuPk}`}><Expanded item={item} meta={meta} root={root} close={close} /></td></tr>}</>;
}
export default function ComparisonTable({ data, root }: { data: Comparison; root: string }) {
  return <div role="region" aria-label="SPU 对比表格滚动区域" tabIndex={0} className="max-w-full overflow-x-auto rounded-xl border">
    <table className="w-full text-sm" aria-label="SPU 两期盈利对比"><thead><tr>{['SPU / 查证', 'ROI（比率）', '净利润（CNY）', '订单数', '广告消耗（CNY）', '事实与限制'].map((label) => <th key={label} scope="col" className="p-3 text-left whitespace-nowrap">{label}</th>)}</tr></thead>
      <tbody>{data.items.map((item) => <Row key={String(item.spuPk)} item={item} meta={data.meta} root={root} />)}</tbody>
    </table>
  </div>;
}
