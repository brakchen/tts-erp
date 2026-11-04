import { Fragment, useEffect, useRef, useState } from 'react';
import { Button, Link, Table } from '@heroui/react';
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
  return href ? <Link href={href}>本期盈利详情（{item.drilldown.startDate} 至 {item.drilldown.endDate}）</Link> : <p>盈利详情链接不可用</p>;
}
function Expanded({ item, meta, root, close }: { item: ComparisonItem; meta: Comparison['meta']; root: string; close: () => void }) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);
  return <section id={`detail-${item.spuPk}`} role="region" aria-labelledby={`detail-title-${item.spuPk}`} className="erp-panel space-y-4 p-3" onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); close(); } }}>
    <h2 ref={heading} tabIndex={-1} id={`detail-title-${item.spuPk}`}>SPU {item.spuId} 对比明细</h2>
    <Button variant="secondary" onPress={close}>关闭 SPU 明细</Button>
    <p>店铺 {item.shopPk} · 内部编号 {item.spuPk} · {item.windowDays} 天 · 截至日 {item.anchorDate}</p>
    <p>本期 {meta.periods.current.startDate} 至 {meta.periods.current.endDate}；上期 {meta.periods.previous.startDate} 至 {meta.periods.previous.endDate}（{meta.reportingTimezone}）</p>
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><Metrics item={item} /></div>
    <Facts item={item} /><DetailLink item={item} root={root} /><p>可通过浏览器返回原查询；估值时点不同不保证两处逐分相同。</p>
  </section>;
}
export default function ComparisonTable({ data, root }: { data: Comparison; root: string }) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const triggers = useRef(new Map<string, HTMLButtonElement>());
  const returningFocus = useRef<string | undefined>(undefined);
  useEffect(() => {
    const id = returningFocus.current;
    if (id === undefined || expanded.has(id)) return;
    returningFocus.current = undefined;
    // Re-entering the grid may restore its row first. Its collection has now
    // committed the collapsed state, so restore the exact opener afterwards.
    triggers.current.get(id)?.focus();
  }, [expanded]);
  function close(id: string) {
    returningFocus.current = id;
    setExpanded((previous) => { const next = new Set(previous); next.delete(id); return next; });
    triggers.current.get(id)?.focus();
  }
  function toggle(id: string) {
    if (expanded.has(id)) close(id);
    else setExpanded((previous) => new Set([...previous, id]));
  }
  return <div className="space-y-4">
    <Table variant="secondary" className="erp-table"><Table.ScrollContainer role="region" aria-label="SPU 对比表格滚动区域" tabIndex={0} className="erp-table-scroll">
      <Table.Content className="w-full text-sm erp-table-content" aria-label="SPU 两期盈利对比"><Table.Header className="erp-table-head">{['SPU / 查证', 'ROI（比率）', '净利润（CNY）', '订单数', '广告消耗（CNY）', '事实与限制'].map((label, index) => <Table.Column key={label} isRowHeader={index === 0} className="p-3 text-left whitespace-nowrap erp-table-column">{label}</Table.Column>)}</Table.Header>
        <Table.Body className="erp-table-body">{data.items.map((item) => <Table.Row key={String(item.spuPk)} id={String(item.spuPk)} className="border-b align-top erp-table-row">
          <Table.Cell className="p-3 text-left erp-table-cell erp-table-row-heading" focusMode="child"><div className="min-w-48 space-y-3"><strong className="break-all">{item.spuId}</strong><p>内部编号 {item.spuPk} · 店铺 {item.shopPk}</p>
            <Button className="erp-table-action" ref={(element) => { if (element) triggers.current.set(String(item.spuPk), element); else triggers.current.delete(String(item.spuPk)); }} size="sm" variant="secondary" aria-expanded={expanded.has(String(item.spuPk))} aria-controls={`detail-${item.spuPk}`} aria-label={`查看对比明细 ${item.spuId}`} onPress={() => toggle(String(item.spuPk))}>查看对比明细</Button><p><DetailLink item={item} root={root} /></p></div></Table.Cell>
          <Table.Cell className="min-w-48 p-3 erp-table-cell"><Metric label="ROI（比率）" previous={item.previousRoi} current={item.currentRoi} previousReason={item.previousRoiUnavailableReason} currentReason={item.currentRoiUnavailableReason} change /></Table.Cell>
          <Table.Cell className="min-w-48 p-3 erp-table-cell"><Metric label="净利润（CNY）" previous={item.previousNetProfitCny} current={item.currentNetProfitCny} change unit="CNY" /></Table.Cell>
          <Table.Cell className="min-w-32 p-3 erp-table-cell"><Metric label="订单数" previous={item.previousOrderCount} current={item.currentOrderCount} /></Table.Cell>
          <Table.Cell className="min-w-48 p-3 erp-table-cell"><Metric label="广告消耗（CNY）" previous={item.previousSpendCny} current={item.currentSpendCny} /></Table.Cell>
          <Table.Cell className="min-w-64 p-3 erp-table-cell"><Facts item={item} /></Table.Cell>
        </Table.Row>)}</Table.Body>
      </Table.Content>
    </Table.ScrollContainer></Table>
    {/* Keep the table collection rectangular and the complete expanded facts
        next to it. Multiple records can stay open, with independent focus return. */}
    {data.items.filter((item) => expanded.has(String(item.spuPk))).map((item) => <Expanded key={String(item.spuPk)} item={item} meta={data.meta} root={root} close={() => close(String(item.spuPk))} />)}
  </div>;
}
