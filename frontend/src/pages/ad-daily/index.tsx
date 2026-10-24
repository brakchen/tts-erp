import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@heroui/react';
import type { PageProps } from '../../shared/contracts';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { decodeList, decodeOptions, type AdList, type Options } from './contracts';
import { displayDecimal, naturalOrder, queryParams, readQuery, type Query, type Sort } from './query';
import AdTable, { displayRoi } from './AdTable';
import Filters from './Filters';

function readFailure(error: unknown): string {
  if (!(error instanceof ApiError)) return error instanceof Error && error.message === '数据格式不符合约定' ? error.message : '网络或响应不可用，请手动重试';
  if (error.status === 401) return '会话已失效（HTTP 401），请重新登录';
  if (error.status === 403) return '无权读取广告数据（HTTP 403）';
  if (error.status === 429) return '请求过于频繁（HTTP 429），请等待后手动重试';
  return `读取失败（HTTP ${error.status}），请手动重试`;
}
export default function AdDaily({ bootstrap }: PageProps) {
  const [query, setQuery] = useState(() => readQuery(location.search));
  const [draft, setDraft] = useState(query);
  const [refresh, setRefresh] = useState(0);
  const [data, setData] = useState<AdList>();
  const [options, setOptions] = useState<Options>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [optionsError, setOptionsError] = useState('');
  const [validation, setValidation] = useState('');
  const [blocked, setBlocked] = useState(false);
  const deadline = useRef(0);
  const cancelDelay = useRef<(() => void) | undefined>(undefined);
  const blockReads = useCallback((error: unknown) => {
    const milliseconds = error instanceof ApiError ? (error.retryAfter ?? 0) * 1000 : 0;
    if (milliseconds <= 0) return;
    deadline.current = Math.max(deadline.current, performance.now() + milliseconds);
    cancelDelay.current?.(); setBlocked(true);
    cancelDelay.current = scheduleReadDelay(() => setBlocked(false), deadline.current - performance.now());
  }, []);
  useEffect(() => () => cancelDelay.current?.(), []);
  useEffect(() => {
    const onPop = () => { const next = readQuery(location.search); setDraft(next); setQuery(next); setValidation(''); };
    window.addEventListener('popstate', onPop); return () => window.removeEventListener('popstate', onPop);
  }, []);
  useEffect(() => {
    if (performance.now() < deadline.current) return;
    let current = true; const controller = new AbortController();
    setOptionsError('');
    requestJson<unknown>(bootstrap.rootPath, '/v2/reporting/ad-daily/options', { signal: controller.signal })
      .then(({ data }) => { const decoded = decodeOptions(data); if (current) setOptions(decoded); })
      .catch((error: unknown) => { if (current) { setOptions(undefined); setOptionsError(`筛选项：${readFailure(error)}`); blockReads(error); } });
    return () => { current = false; controller.abort(); };
  }, [bootstrap.rootPath, refresh, blockReads]);
  useEffect(() => {
    // History can change scope during a cooldown. Hide the old scope, cancel its
    // loading state, and leave the next read to an explicit action after expiry.
    setData(undefined);
    if (performance.now() < deadline.current) { setLoading(false); return; }
    let current = true; const controller = new AbortController();
    setLoading(true); setError('');
    const params = queryParams(query);
    history.replaceState(null, '', `${location.pathname}?${params}${location.hash}`);
    requestJson<unknown>(bootstrap.rootPath, `/v2/reporting/ad-daily?${params}`, { signal: controller.signal })
      .then(({ data }) => {
        const decoded = decodeList(data);
        if (!current) return;
        // Recover a previously valid page after the server's filtered result shrinks.
        if (query.offset > 0 && decoded.total > 0 && query.offset >= decoded.total) {
          setQuery((q) => ({ ...q, offset: Math.floor((decoded.total - 1) / q.limit) * q.limit })); return;
        }
        setData(decoded);
      })
      .catch((error: unknown) => { if (current) { setError(readFailure(error)); blockReads(error); } })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; controller.abort(); };
  }, [bootstrap.rootPath, query, refresh, blockReads]);
  const disabled = loading || blocked;
  function apply() {
    if (disabled) return;
    if (draft.day_from && draft.day_to && draft.day_from > draft.day_to) { setValidation('开始日期不能晚于结束日期'); return; }
    setValidation(''); setQuery({ ...draft, q: draft.q.trim(), offset: 0 });
  }
  function sort(sort: Sort) {
    if (disabled) return;
    const next: Query = { ...query, sort, order: query.sort === sort ? query.order === 'asc' ? 'desc' : 'asc' : naturalOrder(sort), offset: 0 };
    setQuery(next); setDraft((draft) => ({ ...draft, sort: next.sort, order: next.order }));
  }
  return <div className="min-w-0 space-y-6">
    <p>检查插件采集的原始广告事实。金额保持 TikTok 广告源币种 USD，不套用盈利看板汇率。</p>
    <p aria-label="数据日期范围">数据日期范围：{options ? options.min_day && options.max_day ? `${options.min_day} — ${options.max_day}` : '暂无广告日数据' : '—'}</p>
    <Filters draft={draft} setDraft={(next) => { setDraft(next); setValidation(''); }} options={options} disabled={disabled} onApply={apply} onReset={() => { if (!disabled) { setValidation(''); setQuery({ ...readQuery(''), sort: query.sort, order: query.order }); } }} />
    <Button variant="secondary" isDisabled={disabled} onPress={() => { if (!disabled) setRefresh((value) => value + 1); }}>刷新数据</Button>
    {validation && <p role="alert">{validation}</p>}
    {optionsError && <p role="alert">{optionsError}</p>}
    {error && <p role="alert">{error}</p>}
    {blocked && <p role="status">服务端等待期内，读取暂不可用。等待结束后请手动重试。</p>}
    {loading && <p role="status">正在读取广告明细…</p>}
    {data && <>
      <section aria-label="全部筛选结果汇总" className="space-y-3 rounded-xl border p-4">
        <h2 className="text-lg font-semibold">全部筛选结果汇总</h2><p>服务端汇总全部匹配行，不仅是当前页；源币种 USD。</p>
        <dl className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">{[
          ['记录行数', displayDecimal(data.summary.row_count)], ['消耗 USD', displayDecimal(data.summary.spend)],
          ['归因订单', displayDecimal(data.summary.attributed_orders)], ['归因 GMV USD', displayDecimal(data.summary.attributed_gmv)], ['加权 ROI', displayRoi(data.summary.weighted_roi)],
        ].map(([label, value]) => <div key={label} className="min-w-0"><dt className="text-sm">{label}</dt><dd className="break-all text-xl font-semibold tabular-nums">{value}</dd></div>)}</dl>
      </section>
      <AdTable items={data.items} query={query} disabled={disabled} onSort={sort} />
      {data.items.length === 0 && <p role="status">暂无广告日数据</p>}
      <nav aria-label="广告明细分页" className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" isDisabled={disabled || query.offset === 0} onPress={() => setQuery((q) => ({ ...q, offset: Math.max(0, q.offset - q.limit) }))}>上一页</Button>
        <span>{data.total === 0 || data.items.length === 0 ? 0 : query.offset + 1}–{data.items.length === 0 ? 0 : query.offset + data.items.length} / {displayDecimal(data.total)} 行</span>
        <Button variant="secondary" isDisabled={disabled || query.offset + query.limit >= data.total} onPress={() => setQuery((q) => ({ ...q, offset: q.offset + q.limit }))}>下一页</Button>
      </nav>
    </>}
  </div>;
}
