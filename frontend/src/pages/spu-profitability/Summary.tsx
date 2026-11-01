import { useState } from 'react';
import { Button, Modal } from '@heroui/react';
import { optionalRecord, text, type Overview, type RecordValue } from './contracts';
import { displayDecimal } from '../ad-daily/query';

export function displayValue(value: unknown): string { return value == null || value === '' ? '—' : typeof value === 'string' || typeof value === 'number' ? displayDecimal(value) : '—'; }
export function displayPercent(value: unknown): string { if (value == null || value === '') return '—'; const number = Number(value); return Number.isFinite(number) ? `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(number * 100)}%` : '—'; }
export function priceValue(value: unknown, estimated: boolean): string { if (value == null || value === '') return '—'; const raw = String(value); if (!/^-?\d+(\.\d+)?$/.test(raw)) return '—'; const [whole, fraction = ''] = raw.split('.'); return `${estimated ? '≈' : ''}${whole}.${fraction.padEnd(4, '0')}`; }
export const currentMetrics: [string, string, string, boolean?][] = [
  ['总单量', 'total_orders', 'sum-total-orders'], ['广告消耗', 'spend', 'sum-spend'], ['有效单量', 'effective_order_count', 'sum-orders'], ['有效销售', 'effective_sales', 'sum-sales'],
  ['退款数', 'refund_order_count', 'sum-refund-count'], ['退款率', 'refund_rate', 'sum-refund-rate', true], ['全损量', 'full_loss_order_count', 'sum-loss-qty'], ['全损率', 'full_loss_rate', 'sum-loss-rate', true],
  ['国内取消量', 'domestic_cancelled_order_count', 'sum-cancel-count'], ['国内取消率', 'cancel_rate', 'sum-cancel-rate', true], ['净利润', 'net_profit', 'sum-net-profit'], ['实际ROI', 'roi_real', 'sum-roi'],
  ['实际保本ROI', 'roi_breakeven', 'sum-roi-breakeven'], ['广告系统实际ROI', 'ad_system_actual_roi', 'sum-roi-ad-actual'], ['广告系统保本ROI', 'ad_system_breakeven_roi', 'sum-roi-ad'],
];
const projectedMetrics: [string, string, string, boolean?][] = [
  ['已完结样本单', 'projection_completed_basis_order_count', 'sum-projection-completed-basis-orders'], ['已完结全损单', 'projection_completed_full_loss_order_count', 'sum-projection-completed-loss-orders'], ['已完结订单全损率', 'completed_full_loss_rate', 'sum-projection-full-loss-rate', true],
  ['未结算订单', 'unsettled_order_count', 'sum-unresolved-orders'], ['未结算已送达订单', 'delivered_unsettled_order_count', 'sum-delivered-unsettled-orders'], ['待完结风险订单', 'full_loss_exposure_unsettled_order_count', 'sum-full-loss-exposure-unsettled-orders'], ['预计未来新增全损件', 'projected_future_full_loss_qty', 'sum-projected-future-loss-qty'],
  ['预计净收入', 'projected_net_revenue', 'sum-projected-net-revenue'], ['预计净利润', 'projected_net_profit', 'sum-projected-net-profit'], ['预计ROI', 'projected_roi_real', 'sum-projected-roi'], ['预计保本ROI', 'projected_roi_breakeven', 'sum-projected-breakeven-roi'], ['预计广告系统ROI', 'projected_ad_system_actual_roi', 'sum-projected-ad-roi'], ['预计广告系统保本ROI', 'projected_ad_system_breakeven_roi', 'sum-projected-ad-breakeven-roi'],
];
/** Read-only explanations carried over from the approved metric contract; no
 * formula is executed here. Server presentation hints take precedence. */
export const metricHints: Record<string, string> = {
  total_orders: '所选店铺、SPU 范围与经营窗口内按订单去重的总单量。汇总不能由当前页 SPU 行相加。', spend: '当前经营窗口的广告消耗，服务端按同一汇率快照换算为 CNY。', effective_order_count: '有效销售订单数减去其中退款订单数，由服务端去重。', effective_sales: '有效销售订单 GMV 减去退款金额，统一 CNY。',
  refund_order_count: '已完结退款关联的有效订单数，按订单去重。', refund_rate: '退款订单数占当前范围全部订单的比例；无有效分母显示 —。', full_loss_order_count: '服务端当前指标中的全损订单数，保持现有兼容口径；不是独立预测样本的严格全损数。', full_loss_rate: '服务端当前指标的全损率，分母为当前范围全部订单。',
  domestic_cancelled_order_count: '国内取消订单数，排除已归入海外全损的取消订单。', cancel_rate: '国内取消订单数占全部订单的比例，海外取消不重复进入分子。', net_profit: '当前净收入减去货本与广告消耗；已结算使用实际到账，未结算估算由服务端标记。', roi_real: '实际 ROI 使用服务端净收入、全损成本与广告消耗计算，无广告消耗显示 —。', roi_breakeven: '服务端计算的净利润为零时 ROI 临界值；无可解分母时显示 —。',
  ad_system_actual_roi: '广告系统实际ROI = 广告归因GMV ÷ 广告实际消耗；无广告消耗显示 —。', ad_system_breakeven_roi: '广告系统保本ROI = 广告归因GMV ÷ 最大可承受广告费。≈ 表示已知成本下限估算，结算外必要成本尚未完整纳入；无解显示 —。',
  projection_status: '预测可用性由服务端决定，不因切换样本天数自动视为可用。', projection_completed_basis_order_count: '独立成熟样本窗口内的已完结订单数，与当前经营日期窗口分开。', projection_completed_full_loss_order_count: '独立成熟样本中已确认严格全损的订单数。', completed_full_loss_rate: '成熟样本的严格全损订单数占已完结样本订单数的比例。', unsettled_order_count: '当前经营范围内尚未结算的订单数。', delivered_unsettled_order_count: '未结算但已送达或已完成的订单，不再预测未来拒收或配送失败。', full_loss_exposure_unsettled_order_count: '当前经营范围内尚未送达、尚未确认终局全损的未结算风险订单。', projected_future_full_loss_qty: '服务端按成熟样本风险率预测未来新增全损件数，不重复计入已经确认的损失。',
  projected_net_revenue: '服务端提供的预计最终净收入，已结算实际、已送达未结算估算与待完结风险池分开处理。', projected_net_profit: '当前经营范围的预计最终净利润；货本与广告不重复扣除。', projected_roi_real: '预计最终 ROI，仅在大盘展示，不作为单个 SPU 的预测。', projected_roi_breakeven: '服务端按预计净收入与保留货本给出的保本 ROI；样本不足或无解显示 —。', projected_ad_system_actual_roi: '预计广告系统 ROI 使用服务端归因 GMV 与广告消耗；不由浏览器调整归因金额。', projected_ad_system_breakeven_roi: '服务端给出的预计广告系统保本 ROI；分母不可用时显示 —。',
};
export function Metrics({ values, metrics = currentMetrics, prefix = '', meta = {} }: { values: RecordValue; metrics?: typeof currentMetrics; prefix?: string; meta?: RecordValue }) {
  const [hint, setHint] = useState<{ label: string; body: string }>(); const presentation = optionalRecord(meta.presentation); const hints = optionalRecord(presentation.summary_hints); const pnl = optionalRecord(presentation.pnl_hints);
  return <><dl className="erp-profit-metrics">{metrics.map(([label, field, id, percent]) => <div key={field}><dt className="flex items-center gap-1">{label}<Button size="sm" variant="ghost" aria-label={`查看${label}口径`} onPress={() => setHint({ label, body: text(hints[field] ?? (field === 'net_profit' ? pnl.net_profit : field === 'spend' ? pnl.ad_spend : undefined) ?? metricHints[field]) })}>?</Button></dt><dd id={prefix + id} className={field === 'net_profit' && values.profit_status === 'loss' ? 'text-danger' : undefined}>{field === 'ad_system_breakeven_roi' && values.ad_system_breakeven_roi_status === 'estimated_known_costs' && values[field] != null ? '≈' : ''}{percent ? displayPercent(values[field]) : displayValue(values[field])}</dd></div>)}</dl>
    <Modal.Backdrop isOpen={Boolean(hint)} onOpenChange={(open) => { if (!open) setHint(undefined); }}><Modal.Container><Modal.Dialog><Modal.CloseTrigger aria-label="关闭指标说明" /><Modal.Header><Modal.Heading>{hint?.label}口径</Modal.Heading></Modal.Header><Modal.Body><p>{hint?.body}</p></Modal.Body></Modal.Dialog></Modal.Container></Modal.Backdrop></>;
}
export function Summary({ data }: { data: Overview }) {
  const projection = optionalRecord(data.meta.projection); const status: Record<string, string> = { available: '可预测', no_unsettled_orders: '无未结算订单', insufficient_sample: '样本不足' };
  return <section id="summaries" className="erp-panel space-y-4" aria-label="经营汇总" aria-live="polite">
    <div className="flex flex-wrap items-center gap-3"><h2 className="erp-section-heading">当前经营结果</h2><span>金额 CNY · {text(data.totals.profit_status)}</span></div>
    <Metrics values={data.totals} meta={data.meta} metrics={currentMetrics.filter((item) => ['spend', 'effective_sales', 'net_profit', 'roi_real'].includes(item[1]))} />
    <details><summary>订单、退款与 ROI 指标</summary><Metrics values={data.totals} meta={data.meta} metrics={currentMetrics.filter((item) => !['spend', 'effective_sales', 'net_profit', 'roi_real'].includes(item[1]))} /></details>
    <details id="projection-basis-card"><summary className="op-counter-group-label">预测依据与预计结果 · <span className="op-projection-window">预测样本窗口：<span id="projection-sample-window">{text(projection.sample_start)} ~ {text(projection.sample_end)}</span></span></summary>
      <p>预测状态：<span id="sum-projection-status">{status[String(data.totals.projection_status)] ?? text(data.totals.projection_status)}</span> · {metricHints.projection_status}</p><Metrics values={data.totals} meta={data.meta} metrics={projectedMetrics} />
    </details>
  </section>;
}
const priceLabels: Record<string, string> = { purchase: '采购价', originalSale: '销售价', paid: '实付价' };
export function PriceSummary({ data, supported, loading, error }: { data?: Overview; supported: boolean | undefined; loading: boolean; error?: string }) {
  const [tip, setTip] = useState<string>(); const all = optionalRecord(data?.totals.priceStats); const meta = data?.meta ?? {};
  const stats = optionalRecord(tip ? all[tip] : undefined); const fx = optionalRecord(meta.priceFx ?? meta.priceFX); const cost = optionalRecord(meta.priceCost);
  const status = (raw: unknown) => ({ complete: '完整', partial: '部分覆盖', no_samples: '无样本' }[String(raw)] ?? text(raw));
  return <section id="price-summary" hidden={supported !== true} className="erp-panel space-y-3" aria-label="价格统计" aria-busy={loading}>
    <div className="flex flex-wrap items-center gap-3"><h2 className="erp-section-heading">价格 · CNY</h2><span id="price-summary-status">{loading ? '加载中…' : error ? `加载失败 · ${error}` : '已加载'}</span></div>
    <div className="erp-table-scroll"><table className="erp-table-content erp-price-summary-table"><thead><tr><th scope="col">价格口径</th><th scope="col">平均值</th><th scope="col">中位数</th><th scope="col">覆盖</th></tr></thead><tbody>{Object.entries(priceLabels).map(([metric, label]) => { const value = optionalRecord(all[metric]); return <tr key={metric}><th scope="row">{label} <Button size="sm" variant="ghost" data-price-tip={metric} aria-label={`查看${label}统计口径`} onPress={() => setTip(metric)}>?</Button></th>{['mean', 'median'].map((kind) => <td key={kind} id={`price-${metric}-${kind}`} className="erp-table-number">{loading ? '加载中…' : error ? '—' : priceValue(value[kind], metric === 'purchase' && value.estimated === true)}</td>)}<td id={`price-status-${metric}`}>{status(value.status)}</td></tr>; })}</tbody></table></div>
    <Modal.Backdrop isOpen={Boolean(tip)} onOpenChange={(open) => { if (!open) setTip(undefined); }}><Modal.Container size="lg" scroll="inside"><Modal.Dialog><Modal.CloseTrigger aria-label="关闭价格说明" /><Modal.Header><Modal.Heading>{tip ? priceLabels[tip] : ''}统计口径</Modal.Heading></Modal.Header><Modal.Body id="ops-tip" className="space-y-2">
      <p>来源：{tip === 'purchase' ? 'ROI 当前有效成本' : tip === 'originalSale' ? 'TikTok line_items.original_price' : 'TikTok line_items.sale_price'}。按件数加权平均值 / 中位数，当前经营窗口，已付款且排除赠品。</p>
      <p>覆盖：{status(stats.status)} · {text(stats.observedQuantity)} / {text(stats.eligibleQuantity)} 件</p><p>币种：{text(meta.priceCurrency ?? 'CNY')} · 汇率快照：{text(fx.snapshotId ?? fx.snapshot_id)}</p><p>成本基准：{text(cost.basisFingerprint)} · 计算时间：{text(meta.calculatedAt ?? meta.calculated_at)}</p>{stats.estimated === true && <p>≈ 表示服务端估算成本，非人工登记成本。</p>}
    </Modal.Body></Modal.Dialog></Modal.Container></Modal.Backdrop>
  </section>;
}
export function FeeFacts({ fee, dismissed, onDismiss }: { fee: RecordValue; dismissed: boolean; onDismiss: () => void }) {
  const labels: Record<string, string> = { user_override: '覆写', shop_estimate: '实测', baseline: '基线', mixed: '混合' };
  return <section id="fee-card" hidden={dismissed || !fee.source} data-source={text(fee.source)} className="erp-panel space-y-2" aria-label="本次费率依据"><div className="flex flex-wrap items-center gap-3"><strong id="fee-card-source">{labels[String(fee.source)] ?? text(fee.source)}</strong><span id="fee-card-rate">佣金 {displayPercent(fee.rate)}</span>{fee.degraded === true && <span id="fee-card-fallback" title={text(fee.fallback_message)}>已回退基线</span>}<Button id="fee-card-close" size="sm" variant="ghost" aria-label="关闭费率提示" onPress={onDismiss}>关闭</Button></div>
    <div id="fee-card-estimate">{Array.isArray(fee.per_shop) && fee.per_shop.map((raw, index) => { const shop = optionalRecord(raw); const estimate = optionalRecord(shop.estimate); return <p key={index}>{text(shop.shop_name ?? shop.shop_pk)} · {shop.source === 'shop_estimate' ? `${text(estimate.kept_order_count)} 单 · 覆盖 ${displayPercent(estimate.kept_share)} · ${text(estimate.calculated_on)}` : shop.fallback_reason === 'stale_estimate' ? '快照过期' : text(shop.fallback_reason)}</p>; })}</div>
  </section>;
}
