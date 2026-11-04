import { useEffect, useRef, useState } from 'react';
import { Button } from '@heroui/react';
import { pathFor } from '../../shared/api/paths';
import { optionalRecord, text, type ProfitRow, type RecordValue } from './contracts';
import { displayPercent, displayValue, priceValue } from './Summary';
import '../../../../tts_erp_v2/static/vendor/tabulator.min.css';

// Tabulator 6.3.1, MIT, already vendored by this project. The official React
// integration initializes a table inside an empty ref; no wrapper owns page state.
// https://www.tabulator.info/docs/6.x/react/ · https://github.com/olifolkerd/tabulator
interface Cell { getValue(): unknown; getData(): ProfitRow; getField(): string }
interface Row { getData(): ProfitRow; getElement(): HTMLElement }
interface Table {
  on(name: string, handler: (...args: any[]) => void): void;
  replaceData(rows: ProfitRow[]): Promise<unknown>; setSort(sorters: { column: string; dir: string }[]): void;
  getColumns(): { getDefinition(): { field?: string }; show(): void; hide(): void }[];
  updateColumnDefinition(field: string, definition: Record<string, unknown>): Promise<unknown>;
  destroy(): void;
}
type Constructor = new (host: HTMLElement, options: Record<string, unknown>) => Table;
declare global { interface Window { Tabulator?: Constructor } }
const scripts = new Map<string, Promise<Constructor>>();
function loadTable(root: string): Promise<Constructor> {
  if (window.Tabulator) return Promise.resolve(window.Tabulator);
  const url = pathFor(root, '/static/vendor/tabulator.min.js'); const existing = scripts.get(url); if (existing) return existing;
  const promise = new Promise<Constructor>((resolve, reject) => {
    const script = document.createElement('script'); script.src = url; script.async = true;
    script.onload = () => { if (window.Tabulator) resolve(window.Tabulator); else { scripts.delete(url); script.remove(); reject(new Error('表格组件不可用')); } };
    script.onerror = () => { scripts.delete(url); script.remove(); reject(new Error('表格组件加载失败，请刷新重试')); }; document.head.appendChild(script);
  }); scripts.set(url, promise); return promise;
}
export const columns = [
  ['广告消耗', 'spend'], ['广告系统实际ROI', 'ad_system_actual_roi'], ['广告系统保本ROI', 'ad_system_breakeven_roi'], ['有效销售', 'effective_sales'], ['总单量', 'total_orders'], ['有效单量', 'effective_order_count'], ['取消率%', 'cancel_rate'], ['全损率%', 'full_loss_rate'], ['净利润', 'net_profit'],
] as const;
const priceGroups = [['采购价', 'purchase', 'purchasePrice'], ['销售价', 'originalSale', 'originalSalePrice'], ['实付价', 'paid', 'paidPrice']] as const;
export function sortLabel(field: string): string { const metric = columns.find(([, key]) => key === field); if (metric) return metric[0]; if (field === 'roi_real') return '实际ROI'; for (const [label, , prefix] of priceGroups) { if (field === `${prefix}Mean`) return `${label}平均值`; if (field === `${prefix}Median`) return `${label}中位数`; } return field; }
export function ProfitabilityTable({ root, rows, sort, order, prices, meta, loading, error, selectedKey, blocked, onSort, onDrill, onZoom, onRetry }: {
  root: string; rows: ProfitRow[]; sort: string; order: 'asc' | 'desc'; prices: boolean;
  meta: RecordValue; loading: boolean; error?: string; selectedKey: string | null; blocked: boolean; onSort: (sort: string, order: 'asc' | 'desc') => void;
  onDrill: (row: ProfitRow | null, host: HTMLElement | null) => void; onZoom: (url: string) => void; onRetry: () => void;
}) {
  const host = useRef<HTMLDivElement>(null); const table = useRef<Table | undefined>(undefined); const open = useRef<{ key: string; host: HTMLElement; opener: HTMLElement | null } | undefined>(undefined);
  const suppress = useRef(false); const [ready, setReady] = useState(false); const [failed, setFailed] = useState('');
  const [attempt, setAttempt] = useState(0);
  const state = useRef({ rows, sort, order, prices, meta, onSort, onDrill, onZoom, onRetry }); state.current = { rows, sort, order, prices, meta, onSort, onDrill, onZoom, onRetry };
  function close() { state.current.onDrill(null, null); open.current?.opener?.setAttribute('aria-expanded', 'false'); open.current?.host.remove(); open.current = undefined; }
  const frozenWidth = (width: number) => width < 600 ? Math.max(100, Math.min(160, width - 156)) : 280;
  useEffect(() => {
    let current = true; let dispose = () => {}; const element = host.current!; const handled = new WeakSet<Event>();
    void loadTable(root).then((Tabulator) => {
      if (!current) return;
      setFailed('');
      const product = (cell: Cell) => {
        const row = cell.getData(); const wrap = document.createElement('div'); wrap.className = 'erp-profit-product';
        if (row.main_image_url) { const zoom = document.createElement('button'); zoom.type = 'button'; zoom.dataset.zoom = row.main_image_url; zoom.setAttribute('aria-label', `放大图片 ${row.spu_id}`); const image = document.createElement('img'); image.src = row.main_image_url; image.alt = ''; image.width = 48; image.height = 48; image.onerror = () => { image.hidden = true; zoom.textContent = '无主图'; }; zoom.append(image); wrap.append(zoom); }
        else { const missing = document.createElement('span'); missing.className = 'erp-profit-no-image'; missing.textContent = '无主图'; wrap.append(missing); }
        const facts = document.createElement('div'); facts.className = 'erp-profit-product-facts'; const label = document.createElement('button'); label.type = 'button'; label.className = 'erp-profit-detail-trigger'; label.setAttribute('aria-label', `查看商品盈利详情 ${row.spu_id}`); label.setAttribute('aria-expanded', 'false'); label.setAttribute('aria-controls', `spu-drill-${row.spu_pk}`); label.textContent = row.title ?? row.spu_id; label.title = label.textContent; label.setAttribute('aria-description', label.textContent);
        const id = document.createElement('div'); id.className = 'erp-table-id'; id.textContent = row.spu_id; facts.append(id);
        facts.append(label);
        const presentation = optionalRecord(state.current.meta.presentation); const flags: [boolean, string, unknown][] = [[row.uses_default_unit_cost === true, '缺成本', presentation.default_cost_alert_message], [row.refund_rate_alert === true, '高退款', presentation.refund_rate_alert_message], [row.has_unsettled_orders === true, '≈ 含未结算', presentation.unsettled_alert_message], [row.profit_status === 'loss', '亏损', undefined]];
        const tags = document.createElement('div'); tags.className = 'erp-profit-flags'; for (const [show, textValue, tip] of flags) if (show) { const span = document.createElement('span'); span.textContent = textValue; if (tip) span.title = text(tip); tags.append(span); }
        if (row.status && row.status !== 'ACTIVATE') { const tag = document.createElement('span'); tag.textContent = row.status; tags.append(tag); } facts.append(tags); wrap.append(facts); return wrap;
      };
      const formatter = (cell: Cell) => { const row = cell.getData(); const field = cell.getField(); const output = document.createElement('span'); output.textContent = field === 'cancel_rate' || field === 'full_loss_rate' ? displayPercent(cell.getValue()) : `${field === 'ad_system_breakeven_roi' && row.ad_system_breakeven_roi_status === 'estimated_known_costs' && cell.getValue() != null ? '≈' : ''}${displayValue(cell.getValue())}`; return output; };
      const instance = new Tabulator(element, {
        columns: [{ title: '商品', field: 'spu_id', frozen: true, width: frozenWidth(element.clientWidth), minWidth: 100, widthShrink: 0, variableHeight: true, headerSort: false, formatter: product },
          ...columns.map(([title, field]) => ({ title, field, minWidth: 136, hozAlign: 'right', headerHozAlign: 'right', headerSort: true, formatter })),
          ...priceGroups.map(([title, metric]) => ({ title, headerHozAlign: 'center', columns: ['mean', 'median'].map((kind) => ({ title: kind === 'mean' ? '平均值' : '中位数', field: `priceStats.${metric}.${kind}`, minWidth: 120, visible: state.current.prices, hozAlign: 'right', headerHozAlign: 'right', headerSort: true, formatter: (cell: Cell) => priceValue(cell.getValue(), metric === 'purchase' && optionalRecord(optionalRecord(cell.getData().priceStats)[metric]).estimated === true) })) }))],
        data: [], layout: 'fitColumns', renderVertical: 'basic', maxHeight: 'min(72vh, 880px)', sortMode: 'remote', columnDefaults: { headerWordWrap: true, vertAlign: 'middle' }, placeholder: '暂无商品',
        rowFormatter: (row: Row) => { const element = row.getElement(); element.classList.toggle('erp-profit-loss', row.getData().profit_status === 'loss'); },
      }); table.current = instance;
      instance.on('tableBuilt', () => { if (current) setReady(true); });
      instance.on('rowClick', (event: Event, row: Row) => {
        if (handled.has(event)) return; handled.add(event);
        const target = event.target instanceof Element ? event.target.closest<HTMLElement>('[data-zoom]') : null;
        if (target) { state.current.onZoom(target.dataset.zoom!); return; }
        const item = row.getData(); const same = open.current?.key === item.spu_pk; close(); if (same) return;
        const detail = document.createElement('div'); detail.id = `spu-drill-${item.spu_pk}`; detail.className = 'op-drill-row erp-profit-drill'; detail.style.width = `${element.clientWidth}px`; row.getElement().after(detail); const opener = row.getElement().querySelector<HTMLElement>('.erp-profit-detail-trigger'); opener?.setAttribute('aria-expanded', 'true'); open.current = { key: item.spu_pk, host: detail, opener }; state.current.onDrill(item, detail);
      });
      instance.on('dataSorting', (sorters: { field: string; dir: string }[]) => {
        if (suppress.current) return; const sorter = sorters[0]; if (!sorter) return;
        let field = sorter.field; for (const [, metric, prefix] of priceGroups) for (const kind of ['mean', 'median']) if (field === `priceStats.${metric}.${kind}`) field = prefix + (kind === 'mean' ? 'Mean' : 'Median');
        const direction = sorter.dir === 'desc' ? 'desc' : 'asc'; if (field === state.current.sort && direction === state.current.order) return;
        setTimeout(() => { if (current) state.current.onSort(field, direction); }, 0);
      });
      // Make library column headers keyboard sortable using the same remote-state path.
      element.addEventListener('keydown', keyboard);
      function keyboard(event: KeyboardEvent) { if (event.key !== 'Enter' && event.key !== ' ') return; const target = event.target instanceof Element ? event.target : null; if (target?.closest('.tabulator-col[tabulator-field]')) { event.preventDefault(); (target.closest('.tabulator-col') as HTMLElement).click(); } }
      instance.on('renderComplete', () => { for (const column of element.querySelectorAll<HTMLElement>('.tabulator-col[tabulator-field]')) { if (column.getAttribute('tabulator-field') !== 'spu_id') { column.tabIndex = 0; column.setAttribute('role', 'columnheader'); } } });
      let width = frozenWidth(element.clientWidth);
      const resize = new ResizeObserver(() => { if (open.current) open.current.host.style.width = `${element.clientWidth}px`; const next = frozenWidth(element.clientWidth); if (next !== width) { width = next; void instance.updateColumnDefinition('spu_id', { width: next }).catch(() => {}); } }); resize.observe(element);
      dispose = () => { resize.disconnect(); element.removeEventListener('keydown', keyboard); };
    }).catch((error: unknown) => { if (current) setFailed(error instanceof Error ? error.message : '表格加载失败'); });
    return () => { current = false; dispose(); close(); table.current?.destroy(); table.current = undefined; setReady(false); };
  }, [root, attempt]);
  useEffect(() => { if (selectedKey === null && open.current) { const previous = open.current; previous.opener?.setAttribute('aria-expanded', 'false'); previous.host.remove(); open.current = undefined; if (previous.opener?.isConnected) previous.opener.focus(); } }, [selectedKey]);
  useEffect(() => {
    if (!ready || !table.current) return;
    close(); const instance = table.current;
    suppress.current = true;
    let field = sort; for (const [, metric, prefix] of priceGroups) for (const kind of ['mean', 'median']) if (sort === prefix + (kind === 'mean' ? 'Mean' : 'Median')) field = `priceStats.${metric}.${kind}`;
    instance.setSort(field === 'roi_real' ? [] : [{ column: field, dir: order }]); suppress.current = false;
    for (const column of instance.getColumns()) { const key = column.getDefinition().field; if (key?.startsWith('priceStats.')) { if (prices) column.show(); else column.hide(); } }
    void instance.replaceData(rows).then(() => setFailed('')).catch(() => setFailed('表格更新失败，请刷新重试'));
  }, [ready, rows, sort, order, prices]);
  return <div className="min-w-0 space-y-2"><div id="rows" ref={host} className="erp-profit-grid" role="grid" aria-label="SPU 盈利明细表" />
    {loading && <p role="status">加载中…</p>}{(error || failed) && <div role="alert"><p>{error ?? failed}</p><Button type="button" id="retry-link" isDisabled={blocked || loading} onPress={() => { if (blocked || loading) return; if (failed) setAttempt((value) => value + 1); else onRetry(); }}>重试</Button></div>}
  </div>;
}
