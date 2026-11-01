import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Button, Input, Modal } from '@heroui/react';
import PageIntro from '../../shared/PageIntro';
import type { PageProps } from '../../shared/contracts';
import { decodeOverview, decodeShops, optionalRecord, record, text, type ProfitRow, type RecordValue } from './contracts';
import { datePresets, initialQuery, persistQuery, queryParams, readPreferences, reportingDate, reportingZone, shiftDay, type Profile, type Query } from './query';
import { useRead } from './useRead';
import Selection from './Selection';
import { FeeFacts, PriceSummary, Summary } from './Summary';
import { ProfitabilityTable } from './ProfitabilityTable';
import DrillDetails from './DrillDetails';
import './profitability.css';

const emptyRows: ProfitRow[] = [];
function displayTimestamp(value: unknown, region?: string | null): string { if (typeof value !== 'string') return '—'; const date = new Date(value); const zone = reportingZone(region); return Number.isNaN(date.getTime()) || !zone ? value : new Intl.DateTimeFormat('zh-CN', { timeZone: zone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date); }
function ProfitabilityContent({ bootstrap }: PageProps) {
  const root = bootstrap.rootPath; const profile: Profile = bootstrap.pageId === 'focused-spus' ? 'focused-spus' : 'spu-roi'; const focused = profile === 'focused-spus';
  const [query, setQuery] = useState(() => initialQuery(profile, location.search, readPreferences(profile)));
  const [dateStart, setDateStart] = useState(query.wStart); const [dateEnd, setDateEnd] = useState(query.wEnd);
  const [revision, setRevision] = useState(0); const [more, setMore] = useState(false); const [feeDismissed, setFeeDismissed] = useState(false);
  const [shopRevision, setShopRevision] = useState(0); const detailFence = useRef(0);
  const [focusedCount, setFocusedCount] = useState<number | null>(null); const [drill, setDrill] = useState<{ row: ProfitRow; host: HTMLElement }>();
  const [image, setImage] = useState<string>(); const [priceCapability, setPriceCapability] = useState<boolean>();
  const cache = useRef(new Map<string, RecordValue>());
  const shops = useRead(root, '/v2/commerce/channel-accounts?platform=tiktok&limit=500', decodeShops, shopRevision);
  const shop = shops.data?.find((row) => row.id === query.shopPk);
  const today = shop ? reportingDate(new Date(), shop.region) : null;
  const unknownZone = !focused && Boolean(shop) && !today;
  const dateInverted = /^\d{4}-\d{2}-\d{2}$/.test(query.wStart) && /^\d{4}-\d{2}-\d{2}$/.test(query.wEnd) && query.wStart > query.wEnd;
  const automaticDatesReady = focused || query.datesTouched || Boolean(query.wStart && query.wEnd);
  const params = useMemo(() => queryParams(query, focused), [query, focused]);
  const path = shop && !unknownZone && !dateInverted && automaticDatesReady && (!focused || focusedCount !== null) ? `/v2/analytics/spu-roi?${params}` : null;
  const overview = useRead(root, path, decodeOverview, revision);
  const freshness = useRead(root, shop ? `/v2/sync/freshness?shop_pk=${shop.id}` : null, record, revision, 30_000);
  const enums = useRead(root, '/v2/config/enum-map', record);
  const data = overview.data; const blocked = overview.blocked || shops.blocked;
  useEffect(() => { persistQuery(profile, query); }, [profile, query]);
  useEffect(() => {
    if (focused || query.datesTouched || !today || !shop) return;
    const yesterday = shiftDay(today, -1); setQuery((old) => old.shopPk !== shop.id || old.datesTouched ? old : { ...old, wStart: yesterday, wEnd: yesterday });
    setDateStart(yesterday); setDateEnd(yesterday);
  }, [today, shop?.id, focused, query.datesTouched]);
  useEffect(() => { if (data) setPriceCapability(Boolean(data.totals.priceStats || data.items.some((row) => row.priceStats))); }, [data]);
  useEffect(() => { cache.current.clear(); setDrill(undefined); }, [root, path, revision]);
  const focusedChanged = useCallback((count: number | null) => { setFocusedCount(count); setRevision((value) => value + 1); }, []);
  function update(patch: Partial<Query>) {
    if (blocked) return;
    setDrill(undefined); cache.current.clear();
    setQuery((old) => ({ ...old, ...patch, offset: patch.offset ?? 0 }));
  }
  function changeShop(id: string) {
    setFocusedCount(null); setPriceCapability(undefined); update({ shopPk: id, spuIds: [], ...(!query.datesTouched ? { wStart: '', wEnd: '' } : {}) });
    const url = new URL(location.href); url.searchParams.set('shop_pk', id); url.searchParams.delete('spu_ids'); history.replaceState(null, '', url);
  }
  function applyIds(ids: string[]) {
    if (blocked) return;
    update({ spuIds: ids }); const url = new URL(location.href); if (ids.length) url.searchParams.set('spu_ids', ids.join(',')); else url.searchParams.delete('spu_ids'); history.replaceState(null, '', url);
  }
  function commitDates(start: string, end: string) { update({ wStart: start, wEnd: end, datesTouched: true }); }
  function preset(start: string, end: string) {
    setDateStart(start); setDateEnd(end); commitDates(start, end);
  }
  const closeDrill = useCallback(() => { setDrill(undefined); }, []);
  const receiveDrill = useCallback((row: ProfitRow | null, host: HTMLElement | null) => { setDrill(row && host ? { row, host } : undefined); }, []);
  const fee = optionalRecord(data?.meta.fee); const meta = data?.meta ?? {};
  const presetOptions = datePresets(today);
  return <div className="erp-page" data-page-profile={focused ? 'focused-spus' : 'standard-roi'}>
    <PageIntro eyebrow="PRODUCT PERFORMANCE" title={focused ? '关注重点商品，把握经营变化' : '让商品的投入与回报清晰可见'} description="按店铺和经营日期查看商品表现，结合订单、成本与广告核对结果。" />
    <section className="erp-panel erp-profit-controls" aria-label="店铺与经营范围">
      <div className="erp-profit-scope-row"><label className="erp-shop-picker">当前店铺<select id="shop-switcher" className="erp-select" aria-label="当前店铺" value={shop?.id ?? ''} disabled={shops.loading || blocked} onChange={(event) => changeShop(event.target.value)}><option value="" disabled>请选择店铺</option>{shops.data?.map((row) => <option key={row.id} value={row.id}>{row.account_name ?? row.shop_id} · {row.shop_id}</option>)}</select></label><Button variant="secondary" isDisabled={overview.loading || blocked} onPress={() => setRevision((value) => value + 1)}>刷新当前数据</Button></div>
      <div className="erp-profit-dates"><label>起始日<Input fullWidth className="min-w-0" id="filter-w-start" type="date" aria-label="销售/退款起始日期（空 = 不限）" value={dateStart} disabled={blocked} onChange={(event) => { const value = event.target.value; setDateStart(value); if (value) commitDates(value, dateEnd); }} onBlur={(event) => { if (!event.target.value) commitDates('', dateEnd); }} /></label><label>截止日<Input fullWidth className="min-w-0" id="filter-w-end" type="date" aria-label="销售/退款截止日期（空 = 不限）" value={dateEnd} disabled={blocked} onChange={(event) => { const value = event.target.value; setDateEnd(value); if (value) commitDates(dateStart, value); }} onBlur={(event) => { if (!event.target.value) commitDates(dateStart, ''); }} /></label>
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="快捷日期范围">{presetOptions.map(({ id, label, start, end }) => <Button key={id} size="sm" variant="secondary" data-date-preset={id} aria-pressed={query.wStart === start && query.wEnd === end} isDisabled={blocked} onPress={() => preset(start, end)}>{label}</Button>)}</div>
      </div>
      <div className="flex flex-wrap items-end gap-3"><label className="erp-profit-projection">预测样本<select id="filter-projection-lookback-days" aria-label="预测样本天数" className="erp-select" value={query.projectionLookbackDays} disabled={overview.loading || blocked} onChange={(event) => update({ projectionLookbackDays: event.target.value === '90' ? 90 : 30 })}><option value="30">30 天</option><option value="90">90 天</option></select></label><Button id="btn-toolbar-more" size="sm" variant="ghost" isDisabled={blocked} aria-expanded={more} aria-controls="toolbar-more" onPress={() => setMore(!more)}>更多{!more && (query.feePercent || query.includeAll !== focused) ? ' · 已设置' : ''}</Button></div>
      <p className="erp-table-meta">经营日期按店铺当地日；截止日包含当天。清空一端表示不限该端。预测样本窗口由服务端独立给出。</p>
      {more && <div id="toolbar-more" className="flex flex-wrap items-end gap-4"><label>临时佣金费率（%）<Input id="filter-fee" aria-label="临时覆写平台佣金费率（百分比）" inputMode="decimal" value={query.feePercent} disabled={blocked} placeholder="留空使用实测 / 基线" onChange={(event) => update({ feePercent: event.target.value })} /></label><label className="flex min-h-10 items-center gap-2"><input id="filter-include-all" type="checkbox" checked={query.includeAll} disabled={blocked} onChange={(event) => update({ includeAll: event.target.checked })} />含无活动 SPU</label><p>覆写仅影响本次计算，不写回店铺。</p></div>}
      {dateInverted && <p role="alert">起始日期不能晚于截止日期；输入已保留。</p>}{unknownZone && <p role="alert">当前店铺地区无法确定报表时区，请先补充受支持的地区。</p>}
      {blocked && <p role="status">服务端要求等待 Retry-After；到期后请明确刷新。</p>}
    </section>
    {shops.error && <div role="alert"><p>店铺读取失败：{shops.error}</p><Button variant="secondary" isDisabled={shops.loading || shops.blocked} onPress={() => setShopRevision((value) => value + 1)}>重试店铺列表</Button></div>}{shops.loading && <p role="status">加载店铺中…</p>}
    {shop && <Selection key={`${root}:${shop.id}:${profile}`} root={root} shopPk={shop.id} focused={focused} canWrite={bootstrap.roleCapabilities?.readwrite === true} blocked={blocked} applied={query.spuIds} onApply={applyIds} onFocused={focusedChanged} />}
    <section id="data-freshness" className="erp-profit-freshness" aria-label="数据更新" aria-live="polite"><span>数据更新</span>{freshness.error ? <span>同步时间加载失败</span> : Array.isArray(freshness.data?.sources) ? freshness.data.sources.map((raw) => { const source = optionalRecord(raw); const severity = ['ok', 'warn', 'crit', 'unknown'].includes(String(source.severity)) ? String(source.severity) : 'unknown'; return <span key={String(source.key)} data-source={String(source.key)} data-severity={severity} title={`${text(source.detail)} · ${text(source.job_name)} · ${text(source.synced_at)}`}><strong>{text(source.label ?? source.key)}</strong> <time dateTime={typeof source.synced_at === 'string' ? source.synced_at : undefined}>{displayTimestamp(source.synced_at, shop?.region)}</time> · {severity}{source.scope === 'system' ? ' · 全账号' : ''}</span>; }) : <span>—</span>}<span data-source="fx" data-severity="unknown">汇率快照 <time title={text(optionalRecord(meta.fx).as_of_at)}>{displayTimestamp(optionalRecord(meta.fx).as_of_at, shop?.region)}</time></span></section>
    {data && <><FeeFacts fee={fee} dismissed={feeDismissed} onDismiss={() => setFeeDismissed(true)} /><Summary data={data} /></>}
    <PriceSummary data={data} supported={priceCapability} loading={overview.loading} error={overview.error} />
    <section className="erp-panel min-w-0 space-y-4" aria-label="商品经营明细"><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="erp-section-heading">商品经营明细</h2><p>点击商品查看明细；商品列固定，其他指标可横向滚动。</p></div><span id="sort-note">排序：{query.sort} {query.order === 'asc' ? '↑' : '↓'} · {data?.total ?? '—'} 条</span></div>
      {focused && focusedCount === 0 && <p>尚未关注 SPU，请在上方搜索并添加。当前范围仍为关注清单。</p>}
      <ProfitabilityTable root={root} rows={data?.items ?? emptyRows} sort={query.sort} order={query.order} prices={priceCapability === true} meta={meta} loading={overview.loading} error={overview.error} selectedKey={drill?.row.spu_pk ?? null} blocked={blocked} onSort={(sort, order) => update({ sort, order })} onDrill={receiveDrill} onZoom={setImage} onRetry={() => setRevision((value) => value + 1)} />
      <nav className="erp-profit-pagination" aria-label="SPU ROI 分页"><label className="flex items-center gap-2">每页显示<select id="filter-limit" aria-label="每页条数" className="erp-select" value={query.limit} disabled={blocked || overview.loading} onChange={(event) => update({ limit: Number(event.target.value) })}>{[50, 100, 200].map((value) => <option key={value}>{value}</option>)}</select></label><span id="pager-label">第 {Math.floor(query.offset / query.limit) + 1} 页 · 共 {data ? Math.max(1, Math.ceil(data.total / query.limit)) : '—'} 页</span><div className="flex gap-2"><Button variant="secondary" isDisabled={blocked || overview.loading || query.offset === 0} onPress={() => update({ offset: Math.max(0, query.offset - query.limit) })}>上一页</Button><Button variant="secondary" isDisabled={blocked || overview.loading || !data || query.offset + query.limit >= data.total} onPress={() => update({ offset: query.offset + query.limit })}>下一页</Button></div></nav>
    </section>
    {data && <details className="erp-panel space-y-3"><summary>计算依据与数据说明</summary><dl className="grid gap-3 sm:grid-cols-2">{[
      ['计算时间', meta.computed_at ?? meta.calculated_at ?? meta.calculatedAt], ['口径版本', meta.rubric_version], ['金额单位', optionalRecord(meta.currency).display ?? 'CNY'], ['经营日期', `${query.wStart || '不限'} ~ ${query.wEnd || '不限'}（含截止日）`],
      ['源数据覆盖', `${text(optionalRecord(meta.window).coverage_first_day)} ~ ${text(optionalRecord(meta.window).coverage_last_day)}`], ['汇率快照', optionalRecord(meta.fx).snapshot_id ?? optionalRecord(meta.priceFx).snapshotId], ['汇率时间', optionalRecord(meta.fx).as_of_at ?? optionalRecord(meta.fx).as_of], ['成本基准', optionalRecord(meta.priceCost).basisFingerprint],
    ].map(([label, value]) => <div key={String(label)}><dt className="erp-table-meta">{String(label)}</dt><dd className="break-all">{text(value)}</dd></div>)}</dl>
      {Array.isArray(meta.warnings) && meta.warnings.length > 0 && <div><h3 className="font-medium">数据提示</h3><ul className="list-inside list-disc">{meta.warnings.map((warning, index) => <li key={index}>{typeof warning === 'string' ? warning : text(optionalRecord(warning).message ?? optionalRecord(warning).detail ?? optionalRecord(warning).code)}</li>)}</ul></div>}
      <p>当前指标与预计结果均使用服务端计算值；分页和排序不改变汇总范围。预测样本窗口独立于经营日期。</p>
    </details>}
    {drill && createPortal(<DrillDetails key={drill.row.spu_pk} root={root} row={drill.row} meta={meta} enums={enums.data ?? {}} start={query.wStart} end={query.wEnd} cache={cache.current} fence={detailFence} onClose={closeDrill} />, drill.host)}
    <Modal.Backdrop isOpen={Boolean(image)} onOpenChange={(open) => { if (!open) setImage(undefined); }}><Modal.Container size="lg"><Modal.Dialog><Modal.CloseTrigger aria-label="关闭图片" /><Modal.Header><Modal.Heading>商品图片</Modal.Heading></Modal.Header><Modal.Body>{image && <img src={image} alt="商品主图放大" className="max-h-[70vh] max-w-full object-contain" />}</Modal.Body></Modal.Dialog></Modal.Container></Modal.Backdrop>
    <Modal.Backdrop isOpen={Boolean(shops.data && !shop)} isDismissable={false}><Modal.Container><Modal.Dialog><Modal.Header><Modal.Heading>请选择店铺</Modal.Heading></Modal.Header><Modal.Body><p>{query.shopPk ? '原店铺不可用，请重新选择。' : '请先选择当前经营店铺。'}</p><div id="shop-modal-list" className="max-h-[50vh] space-y-2 overflow-y-auto">{shops.data?.length ? shops.data.map((row) => <Button className="h-auto w-full justify-start py-3 whitespace-normal" key={row.id} variant="secondary" onPress={() => changeShop(row.id)}><span className="min-w-0 break-words text-left">{row.account_name ?? row.shop_id}<span className="block break-all text-xs">{row.shop_id}</span></span></Button>) : <p>暂无可用店铺。</p>}</div></Modal.Body></Modal.Dialog></Modal.Container></Modal.Backdrop>
  </div>;
}
export default function ProfitabilityPage(props: PageProps) { return <ProfitabilityContent key={`${props.bootstrap.rootPath}:${props.bootstrap.pageId}`} {...props} />; }
