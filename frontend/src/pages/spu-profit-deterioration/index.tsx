import OffsetPagination from '../../shared/OffsetPagination';
import PageIntro from '../../shared/PageIntro';
import { useEffect, useRef, useState } from 'react';
import { Button, Input, Label, ListBox, Select } from '@heroui/react';
import type { PageProps } from '../../shared/contracts';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { parseIdentityJson } from '../../shared/api/identity-json';
import { decodeComparison, decodeShops, isoDate, type Comparison, type Shop } from './contracts';
import { queryError, queryParams, readQuery, type Query } from './query';
import ComparisonTable, { displayValue } from './ComparisonTable';
import CoverageDetails from './CoverageDetails';
function readFailure(error: unknown): string {
  if (!(error instanceof ApiError)) return error instanceof Error && ['数据格式不符合约定', '没有可用店铺，请确认访问权限；未自动切换店铺。', '请求的店铺不可用或无权限，请选择店铺；未自动切换。'].includes(error.message) ? error.message : '网络或响应不可用，请手动重试。';
  const label = { 401: '会话已失效，请重新登录', 403: '无权限读取此范围', 422: '参数或日期错误，请检查输入', 429: '请求过于频繁，等待结束后请手动重试', 503: '数据不可用，请重试' }[error.status] ?? '读取失败，请重试';
  return `${label}（HTTP ${error.status}）`;
}
function safeRequestId(value: unknown): string | undefined { return typeof value === 'string' && /^[a-zA-Z0-9_.:-]{1,160}$/.test(value) ? value : undefined; }
function errorRequestId(error: unknown): string | undefined {
  if (!(error instanceof ApiError)) return;
  const detail = error.detail;
  return safeRequestId(error.requestId) || (detail !== null && typeof detail === 'object' && 'requestId' in detail ? safeRequestId(detail.requestId) : undefined);
}
function validDate(value: string): boolean { try { isoDate(value); return true; } catch { return false; } }
function latestDate(error: unknown): string | undefined {
  if (!(error instanceof ApiError) || error.status !== 422 || !error.detail || typeof error.detail !== 'object' || !('latestAvailableDate' in error.detail)) return;
  try { return isoDate(error.detail.latestAvailableDate); } catch { return; }
}
export default function SpuProfitDeterioration({ bootstrap }: PageProps) {
  const [query, setQuery] = useState(() => readQuery(location.search));
  const [draft, setDraft] = useState(query);
  const [refresh, setRefresh] = useState(0);
  const [shops, setShops] = useState<Shop[]>();
  const shopsCache = useRef<{ root: string; shops: Shop[] } | undefined>(undefined);
  const [data, setData] = useState<Comparison>();
  const [applied, setApplied] = useState<Query>();
  const resolved = useRef<{ source: Query; query: Query } | undefined>(undefined);
  const [bound, setBound] = useState<{ shop: string; date: string }>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(''); const [requestId, setRequestId] = useState<string>();
  const [validation, setValidation] = useState(''); const [blocked, setBlocked] = useState(false);
  const deadline = useRef(0); const cancelDelay = useRef<(() => void) | undefined>(undefined);
  useEffect(() => () => cancelDelay.current?.(), []);
  useEffect(() => {
    const onPop = () => { const next = readQuery(location.search); setDraft(next); setQuery(next); setValidation(''); };
    window.addEventListener('popstate', onPop); return () => window.removeEventListener('popstate', onPop);
  }, []);
  useEffect(() => {
    let current = true; const controller = new AbortController();
    setData(undefined); setApplied(undefined); setLoading(false); setError(''); setRequestId(undefined);
    if (performance.now() < deadline.current) return;
    const source = resolved.current?.source === query ? resolved.current.query : query;
    const invalid = queryError(source);
    if (invalid) { setError(invalid); return; }
    setLoading(true);
    async function load() {
      let q = source;
      try {
        let available = shopsCache.current?.root === bootstrap.rootPath ? shopsCache.current.shops : undefined;
        if (!available) {
          // Financial DTOs contain only decimal strings; remaining numeric tokens are identities/counts.
          const response = await requestJson<unknown>(bootstrap.rootPath, '/v2/commerce/channel-accounts?platform=tiktok&limit=500', { signal: controller.signal }, parseIdentityJson);
          available = decodeShops(response.data);
          if (!current) return;
          shopsCache.current = { root: bootstrap.rootPath, shops: available }; setShops(available);
        }
        if (!available.length) throw new Error('没有可用店铺，请确认访问权限；未自动切换店铺。');
        if (!q.shop) {
          q = { ...q, shop: String(available[0].id) };
          setDraft((d) => d.shop === source.shop ? { ...d, shop: q.shop } : d);
        }
        if (!available.some((s) => String(s.id) === q.shop)) throw new Error('请求的店铺不可用或无权限，请选择店铺；未自动切换。');
        const response = await requestJson<unknown>(bootstrap.rootPath, `/v2/analytics/spu-profit-deterioration?${queryParams(q)}`, { signal: controller.signal }, parseIdentityJson);
        const payload = decodeComparison(response.data);
        if (!current) return;
        const effective = { ...q, anchor: q.anchor || payload.meta.anchorDate };
        resolved.current = { source: query, query: effective };
        setBound({ shop: q.shop, date: payload.meta.latestAvailableDate });
        // Defaults fill only untouched fields, never overwriting edits made during loading.
        setDraft((d) => d.shop === q.shop ? { ...d, anchor: d.anchor === source.anchor ? effective.anchor : d.anchor } : d);
        history.replaceState(null, '', `${location.pathname}?${queryParams(effective)}${location.hash}`);
        setApplied(effective); setData(payload);
      } catch (error: unknown) {
        if (!current) return;
        setError(readFailure(error)); setRequestId(errorRequestId(error));
        const date = latestDate(error); if (date) setBound({ shop: q.shop, date });
        const milliseconds = error instanceof ApiError ? (error.retryAfter ?? 0) * 1000 : 0;
        if (milliseconds > 0) {
          deadline.current = Math.max(deadline.current, performance.now() + milliseconds);
          cancelDelay.current?.(); setBlocked(true);
          cancelDelay.current = scheduleReadDelay(() => setBlocked(false), deadline.current - performance.now());
        }
      } finally { if (current) setLoading(false); }
    }
    void load();
    return () => { current = false; controller.abort(); };
  }, [bootstrap.rootPath, query, refresh]);
  function update(next: Query) { setDraft(next); setValidation(''); }
  function commit(next: Query) {
    if (blocked) return;
    setValidation(''); setQuery(next);
    history.pushState(null, '', `${location.pathname}?${queryParams(next)}${location.hash}`);
  }
  function apply() {
    if (blocked) return;
    const next = { ...draft, offset: '0' };
    if (!next.shop) { setValidation('请选择店铺。'); return; }
    const invalid = queryError(next, bound);
    if (invalid) { setValidation(invalid); return; } commit(next);
  }
  function page(offset: number) {
    if (!applied || blocked || loading || !Number.isSafeInteger(offset)) return;
    commit({ ...applied, offset: String(offset) });
  }
  const dirty = applied !== undefined && ['shop', 'spus', 'window', 'activity', 'anchor', 'limit'].some((key) => draft[key as keyof Query] !== applied[key as keyof Query]);
  const dateMax = bound?.shop === draft.shop ? bound.date : undefined;
  return <div className="erp-page">
    <PageIntro eyebrow="PROFIT MONITORING" title="及时发现利润的变化" description="选择店铺和分析日期，对比商品表现，查看服务端分析结果。" />
    <p>SPU 盈利表现对比：7 天主参考，3 天观察近期变化；不做阈值告警或投放建议。</p>
    <form aria-label="对比范围" className="erp-filters" onSubmit={(event) => { event.preventDefault(); apply(); }}>
        <Select className="w-60 min-w-0" aria-label="店铺" value={`choice:${draft.shop}`} onChange={(key) => { if (typeof key === 'string') { update({ ...draft, shop: key.slice(7) }); } } }><Label>店铺</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox><ListBox.Item id="choice:" textValue="选择店铺">选择店铺</ListBox.Item>{draft.shop && !shops?.some((s) => String(s.id) === draft.shop) && <ListBox.Item key={draft.shop} id={`choice:${draft.shop}`} textValue={`请求的店铺 ${draft.shop}（待确认）`}>{`请求的店铺 ${draft.shop}（待确认）`}</ListBox.Item>}{shops?.map((s) => <ListBox.Item key={String(s.id)} id={`choice:${String(s.id)}`} textValue={s.account_name || s.shop_id || String(s.id)}>{s.account_name || s.shop_id || String(s.id)}</ListBox.Item>)}</ListBox></Select.Popover></Select>
        <label className="w-52 min-w-0">精确 SPU 内部编号<Input fullWidth className="min-w-0" aria-label="精确 SPU 内部编号" placeholder="留空使用活动范围；如 1001,1002" value={draft.spus} onChange={(event) => update({ ...draft, spus: event.target.value })} /></label>
        <Select className="w-36 min-w-0" aria-label="比较窗口" value={`choice:${draft.window}`} onChange={(key) => { if (typeof key === 'string') { update({ ...draft, window: key.slice(7) }); } } }><Label>比较窗口</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox>{!['7', '3'].includes(draft.window) && <ListBox.Item key={draft.window} id={`choice:${draft.window}`} textValue={`${draft.window}（无效）`}>{`${draft.window}（无效）`}</ListBox.Item>}<ListBox.Item id="choice:7" textValue="7 天主参考">7 天主参考</ListBox.Item><ListBox.Item id="choice:3" textValue="3 天近期变化">3 天近期变化</ListBox.Item></ListBox></Select.Popover></Select>
        <label className="w-44 min-w-0">截至日（店铺当地日）<Input fullWidth className="min-w-0" aria-label="截至日（店铺当地日）" type={!draft.anchor || validDate(draft.anchor) ? 'date' : 'text'} max={dateMax} value={draft.anchor} onChange={(event) => update({ ...draft, anchor: event.target.value })} /></label>
        <Select className="w-72 min-w-0" aria-label="活动范围" value={`choice:${draft.activity}`} onChange={(key) => { if (typeof key === 'string') { update({ ...draft, activity: key.slice(7) }); } } }><Label>活动范围</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox>{!['recent', 'all'].includes(draft.activity) && <ListBox.Item key={draft.activity} id={`choice:${draft.activity}`} textValue={`${draft.activity}（无效）`}>{`${draft.activity}（无效）`}</ListBox.Item>}<ListBox.Item id="choice:recent" textValue="固定近 7 天活动范围（保留无单投放）">固定近 7 天活动范围（保留无单投放）</ListBox.Item><ListBox.Item id="choice:all" textValue="全部本店 SPU 调查">全部本店 SPU 调查</ListBox.Item></ListBox></Select.Popover></Select>
        <Select className="w-24 min-w-0" aria-label="每页条数" value={`choice:${draft.limit}`} onChange={(key) => { if (typeof key === 'string') { update({ ...draft, limit: key.slice(7) }); } } }><Label>每页条数</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox>{!['50', '100', '200'].includes(draft.limit) && <ListBox.Item key={draft.limit} id={`choice:${draft.limit}`} textValue={draft.limit}>{draft.limit}</ListBox.Item>}{['50', '100', '200'].map((v) => <ListBox.Item key={v} id={`choice:${v}`} textValue={v}>{v}</ListBox.Item>)}</ListBox></Select.Popover></Select>
      <div className="flex flex-wrap gap-3"><Button type="submit" isDisabled={blocked}>应用筛选</Button><Button variant="secondary" isDisabled={blocked} onPress={() => { const next = readQuery(''); update(next); commit(next); }}>重置筛选</Button><Button variant="secondary" isDisabled={blocked || loading} onPress={() => { if (!blocked) setRefresh((v) => v + 1); }}>刷新 / 重试</Button></div>
    </form>
    <p>{dateMax ? `最新可用日期：${dateMax}（店铺当地完整日）` : '等待后台确认此店铺的最新可用日期。'}；可查历史日期，刷新只重新读取，不触发同步。</p>
    {dirty && <p role="status">当前输入尚未应用；下方仅为已应用范围，点击应用筛选后更新。</p>}
    {validation && <p role="alert">{validation}</p>}{error && <p role="alert">{error}{requestId ? ` · 请求编号：${requestId}` : ''}</p>}
    {blocked && <p role="status">服务端等待期内，读取暂不可用。等待结束后请手动重试。</p>}
    {loading && <p role="status">正在加载当前范围…</p>}
    {!loading && !data && <p>当前条件未取得可用结果；不展示旧条件数据。</p>}
    {data && applied && <>
      <section aria-label="已应用对比范围" className="erp-panel grid gap-x-6 gap-y-2 lg:grid-cols-2">
        <p>{data.meta.scopeDescription}{data.meta.activityBypassed ? '；精确调查：活动排除不生效。' : ''}</p>
        <p>店铺 {data.meta.shopPk} · {data.meta.windowDays} 天 · 截至日 {data.meta.anchorDate}</p>
        <p>本期 {data.meta.periods.current.startDate} 至 {data.meta.periods.current.endDate}；上期 {data.meta.periods.previous.startDate} 至 {data.meta.periods.previous.endDate}（含首尾，{data.meta.reportingTimezone}）</p>
        <p>{data.meta.windowDays === 3 ? '3 天用于观察近期变化，易受少量订单和投放节奏影响，有短期波动；不单独代表持续改善或恶化。' : '7 天为主参考，覆盖完整星期，但累计订单不等于两期样本充分；3 天与 7 天方向可不同。'}</p>
        <p role="status">{data.meta.stale ? '数据未更新 · ' : ''}实际数据日期：{data.meta.dataAsOf}；计算时间：{data.meta.calculatedAt}（不代表数据更新）。</p>
        <p>无单但有广告费的记录保留并重点标注。ROI 可计算的正数、零和负数均正常展示；不可计算时显示原因。变化为展示值，不构成经营决策。</p>
        {data.meta.warnings.map((note, index) => <p key={index}>{note}</p>)}<p>请求编号：{safeRequestId(data.meta.requestId) || '未提供'}</p>
      </section>
      <section role="region" aria-label="完整分析范围统计" className="space-y-4 rounded-xl border p-4">
        <h2 className="text-lg font-semibold">完整分析范围统计</h2><dl className="grid gap-4 sm:grid-cols-3">{[['分析 SPU 数', data.totals.analysisSpuCount], ['本期订单数', data.totals.currentOrderCount], ['上期订单数', data.totals.previousOrderCount]].map(([label, value]) => <div key={label as string}><dt>{label}</dt><dd className="break-all text-xl font-semibold tabular-nums">{displayValue(value as number | null)}</dd></div>)}</dl>
        <p>{data.totals.unavailableReason || '完整范围：SPU 去重，每期订单跨 SPU 全局去重；不是当前页或行级订单相加。'}</p>
      </section>
      <CoverageDetails coverage={data.meta.coverage} />
      {data.items.length > 0 ? <ComparisonTable root={bootstrap.rootPath} data={data} /> : <p role="status">当前条件无匹配 SPU，请调整范围或日期；不代表全店正常或无风险。</p>}
      <OffsetPagination label="对比分页" offset={Number(applied.offset)} pageSize={Number(applied.limit)}
        total={data.total} disabled={blocked || loading} onPage={page}
        summaryAddon={<span>{data.total} 条对比记录</span>} />
    </>}
  </div>;
}
