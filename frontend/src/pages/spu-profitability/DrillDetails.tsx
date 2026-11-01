import { useEffect, useState } from 'react';
import { Button, Tabs } from '@heroui/react';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { parseIdentityJson } from '../../shared/api/identity-json';
import { withinRequestLifetime } from '../../shared/api/request-lifetime';
import { optionalRecord, record, text, type ProfitRow, type RecordValue } from './contracts';
import { displayPercent, displayValue, Metrics } from './Summary';

type Tab = 'pnl' | 'orders' | 'settlements' | 'cases' | 'ads';
const tabs: [Tab, string][] = [['pnl', '利润构成'], ['orders', '订单·物流'], ['settlements', '结算'], ['cases', '售后'], ['ads', '广告']];
export default function DrillDetails({ root, row, meta, enums, start, end, cache, fence, onClose }: {
  root: string; row: ProfitRow; meta: RecordValue; enums: RecordValue; start: string; end: string; cache: Map<string, RecordValue>; fence: { current: number }; onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>('pnl'); const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{ key: string; data?: RecordValue; error?: string; loading?: boolean }>({ key: 'pnl' });
  const [blocked, setBlocked] = useState(false);
  const params = new URLSearchParams(); if (start) params.set('w_start', start); if (end) params.set('w_end', end);
  const path = `/v2/analytics/spu-roi/${encodeURIComponent(row.spu_pk)}/${tab}${params.size ? `?${params}` : ''}`;
  const key = root + path;
  useEffect(() => {
    if (tab === 'pnl') return;
    const hit = cache.get(key); if (hit) { setState({ key, data: hit }); return; }
    const controller = new AbortController(); let current = true; let cancelDelay: (() => void) | undefined;
    if (performance.now() < fence.current) { setBlocked(true); setState({ key, error: '服务端要求等待 Retry-After 后再明确重试' }); cancelDelay = scheduleReadDelay(() => setBlocked(false), fence.current - performance.now()); return () => cancelDelay?.(); }
    setBlocked(false); setState({ key, loading: true });
    void withinRequestLifetime(controller, (signal) => requestJson<unknown>(root, path, { signal }, parseIdentityJson)).then(({ data }) => {
      const result = record(data); if (!Array.isArray(result[tab])) throw new Error('明细结构不完整');
      if (current) { cache.set(key, result); setState({ key, data: result }); }
    }).catch((error: unknown) => { if (current) { if (error instanceof ApiError && error.retryAfter) { fence.current = Math.max(fence.current, performance.now() + error.retryAfter * 1000); setBlocked(true); cancelDelay = scheduleReadDelay(() => setBlocked(false), fence.current - performance.now()); } setState({ key, error: error instanceof Error ? error.message : '明细读取失败' }); } });
    return () => { current = false; controller.abort(); cancelDelay?.(); };
  }, [root, path, key, tab, revision, cache, fence]);
  useEffect(() => { const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); }; document.addEventListener('keydown', escape); return () => document.removeEventListener('keydown', escape); }, [onClose]);
  const presentation = optionalRecord(meta.presentation); const hints = optionalRecord(presentation.pnl_hints);
  const tr = (kind: string, value: unknown) => text(optionalRecord(enums[kind])[String(value)] ?? value);
  const data = state.key === key ? state.data : undefined; const rows = data && Array.isArray(data[tab]) ? data[tab] as unknown[] : []; const detailMeta = optionalRecord(data?.meta);
  const pnlRows = [
    ['净收入 · 已结算', 'settled_net', 'settled'], ['净收入 · 未结算', 'unsettled_net', 'unsettled'], ['货本 · 售出件', 'cogs_sold', 'cogs_sold'], ['货本 · 全损取消件', 'cogs_full_loss_cancelled', 'cogs_full_loss'], ['广告消耗', 'spend', 'ad_spend'], ['净利润', 'net_profit', 'net_profit'],
  ];
  return <section aria-label={`商品盈利详情 ${row.spu_id}`} className="erp-panel space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="erp-section-heading">{row.title ?? row.spu_id}</h3><p className="break-all">{row.spu_id} · 内部 PK {row.spu_pk}</p></div><Button size="sm" variant="secondary" onPress={onClose}>收起商品详情</Button></div>
    <Tabs selectedKey={tab} onSelectionChange={(key) => setTab(String(key) as Tab)}><Tabs.List aria-label="盈利查证明细" className="op-drill-tabs flex flex-wrap gap-2">{tabs.map(([id, label]) => <Tabs.Tab key={id} id={id} data-tab={id}>{label}</Tabs.Tab>)}</Tabs.List>
    <Tabs.Panel id={tab} className="op-drill-body space-y-4">
      {tab === 'pnl' ? <>
        {row.has_unsettled_orders === true && <p role="note">≈ {text(presentation.unsettled_alert_message ?? '含未结算订单，使用服务端提供的估算结果。')}</p>}
        <Metrics values={row} meta={meta} prefix={`detail-${row.spu_pk}-`} />
        <div className="erp-table-scroll"><table className="erp-table-content"><thead><tr><th scope="col" className="erp-table-column">利润层级</th><th scope="col" className="erp-table-column erp-table-number">金额 CNY</th><th scope="col" className="erp-table-column">服务端依据</th></tr></thead><tbody>{pnlRows.map(([label, field, hint]) => <tr key={field}><th scope="row" className="erp-table-cell">{label}{field === 'cogs_sold' && row.units_sold != null && row.unit_cost_used != null && <p className="erp-table-meta">{displayValue(row.units_sold)} 件 × {displayValue(row.unit_cost_used)}</p>}{field === 'cogs_full_loss_cancelled' && row.full_loss_cancelled_qty != null && row.unit_cost_used != null && <p className="erp-table-meta">{displayValue(row.full_loss_cancelled_qty)} 件 × {displayValue(row.unit_cost_used)}</p>}</th><td className="erp-table-cell erp-table-number">{displayValue(row[field])}</td><td className="erp-table-cell">{text(hints[hint])}</td></tr>)}</tbody></table></div>
      </> : <>
        {(state.key !== key || state.loading) && <p role="status">加载明细中…</p>}{state.key === key && state.error && <div role="alert"><p>{state.error}</p><Button variant="secondary" isDisabled={blocked} onPress={() => { if (performance.now() >= fence.current) setRevision((value) => value + 1); }}>重试明细</Button></div>}
        {tab === 'settlements' && <p>本 SPU 占单比：本 SPU 行金额 ÷ 整单金额，与给平台分摊多少钱无关。</p>}
        {data && <><p>证据时间：{text(detailMeta.computed_at)} · {text(detailMeta.rubric_version)} · {text(optionalRecord(detailMeta.currency).display ?? 'CNY')}</p>{detailMeta.orders_truncated === true && <p role="note">订单明细已截断，最多展示 500 条。</p>}{detailMeta.note != null && <p>{text(detailMeta.note)}</p>}
          {!rows.length ? <p>{tab === 'settlements' ? '暂无结算；未结算订单不展示，估算结果见利润构成。' : tab === 'ads' ? '无广告投放' : '暂无明细'}</p> : <div className="erp-table-scroll" role="region" aria-label={`${tabs.find(([id]) => id === tab)?.[1]}表格`} tabIndex={0}>
            <table className="erp-table-content erp-profit-evidence"><thead><tr>{(tab === 'orders' ? ['订单号', '状态', '件', '已结算', '已到海外', '全损'] : tab === 'settlements' ? ['订单号', '订单状态', '结算金额 CNY', '本 SPU 占单比', '对账单时间'] : tab === 'cases' ? ['售后单号', '订单号', '类型', '状态', '退款 CNY'] : ['计划 ID', '消耗 CNY', '订单', '日期范围']).map((label) => <th key={label} scope="col" className="erp-table-column">{label}</th>)}</tr></thead>
              <tbody>{rows.map((raw, index) => { const item = record(raw); const components = Array.isArray(item.components) ? item.components.map(optionalRecord) : []; const settlement = components.find((entry) => entry.code === 'SETTLEMENT');
                const cells = tab === 'orders' ? [text(item.order_id), tr('order_status', item.status), displayValue(item.qty), item.is_settled === true ? '是' : '否', item.arrived_overseas === true ? '是' : '否', item.full_loss === true ? '是' : '否']
                  : tab === 'settlements' ? [text(item.order_id), tr('order_status', item.status), settlement ? displayValue(settlement.amount) + (String(settlement.amount) === '0' || /^0\.0+$/.test(String(settlement.amount)) ? item.status === 'CANCELLED' ? '（已取消 / 冲销）' : '（无入账）' : '') : '—', displayPercent(item.share_ratio), text(item.statement_time)]
                    : tab === 'cases' ? [text(item.case_id), text(item.order_id), tr('case_type', item.type), tr('case_status', item.status), displayValue(item.refund_amount)]
                      : [text(item.campaign_id), displayValue(item.spend), displayValue(item.orders), `${text(item.first_day)} ~ ${text(item.last_day)}`];
                return <tr key={index} className="erp-table-row">{cells.map((value, column) => <td key={column} className="erp-table-cell">{value}</td>)}</tr>;
              })}</tbody></table>
          </div>}
        </>}
      </>}
    </Tabs.Panel></Tabs>
  </section>;
}
