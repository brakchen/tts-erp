import PageIntro from '../../shared/PageIntro';
import { useEffect, useState } from 'react';
import { Alert, Button, Table } from '@heroui/react';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import type { PageProps } from '../../shared/contracts';
import DailyChart, { type DailyCount } from './DailyChart';

interface Distribution { label: string; count: number; percentage: string }
interface Stats {
  total_requests: number; whitelisted_requests: number; today_requests: number; error_requests: number;
  by_host: Distribution[]; by_method: Distribution[]; by_status: Distribution[]; daily: DailyCount[];
}
const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
function decode(value: unknown): Stats {
  if (!record(value) || !['total_requests', 'whitelisted_requests', 'today_requests', 'error_requests'].every((key) => count(value[key])) ||
      !['by_host', 'by_method', 'by_status'].every((key) => Array.isArray(value[key]) && value[key].every((row: unknown) => record(row) && typeof row.label === 'string' && count(row.count) && typeof row.percentage === 'string' && /^\d+(\.\d+)?$/.test(row.percentage))) ||
      !Array.isArray(value.daily) || !value.daily.every((row: unknown) => {
        if (!record(row) || !count(row.count) || typeof row.date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(row.date)) return false;
        const timestamp = Date.parse(`${row.date}T00:00:00Z`);
        return Number.isFinite(timestamp) && new Date(timestamp).toISOString().slice(0, 10) === row.date;
      })) throw new Error('统计响应格式错误');
  return value as unknown as Stats;
}
const number = (value: number) => value.toLocaleString('en-US');
function DistributionTable({ title, rows }: { title: string; rows: Distribution[] }) {
  return <section className="erp-panel min-w-0 space-y-2"><h2>{title}分布</h2><Table variant="secondary" className="erp-table"><Table.ScrollContainer className="erp-table-scroll" role="region" aria-label="数据表格横向滚动区域" tabIndex={0}><Table.Content className="erp-table-content" aria-label={`${title}分布`}>
    <Table.Header className="erp-table-head"><Table.Column className="erp-table-column" isRowHeader>{title}</Table.Column><Table.Column className="erp-table-column erp-table-number">请求数</Table.Column><Table.Column className="erp-table-column erp-table-number">占比（服务端）</Table.Column></Table.Header>
    <Table.Body className="erp-table-body" renderEmptyState={() => <div className="erp-table-empty">{`暂无${title}数据`}</div>}>{rows.map((row, index) => <Table.Row className="erp-table-row" key={index} id={index}>
      <Table.Cell className="erp-table-cell"><span className="break-all">{row.label}</span></Table.Cell><Table.Cell className="erp-table-cell erp-table-number">{number(row.count)}</Table.Cell><Table.Cell className="erp-table-cell erp-table-number">{row.percentage}%</Table.Cell>
    </Table.Row>)}</Table.Body>
  </Table.Content></Table.ScrollContainer></Table></section>;
}

export default function InterceptStats({ bootstrap }: PageProps) {
  const [refresh, setRefresh] = useState(0);
  const [state, setState] = useState<{ loading: boolean; data?: Stats; error?: string; blocked?: boolean }>({ loading: true });
  useEffect(() => {
    const controller = new AbortController(); let current = true; let stopDelay: (() => void) | undefined;
    setState({ loading: true });
    requestJson<unknown>(bootstrap.rootPath, '/v2/intercept/requests/stats?days=7', { signal: controller.signal })
      .then(({ data }) => { const decoded = decode(data); if (current) setState({ loading: false, data: decoded }); })
      .catch((error: unknown) => {
        if (!current) return;
        const delay = error instanceof ApiError ? (error.retryAfter ?? 0) * 1000 : 0;
        const message = error instanceof ApiError ? `统计加载失败（HTTP ${error.status}）${error.requestId ? ` · 请求 ${error.requestId}` : ''}`
          : error instanceof Error && error.message === '统计响应格式错误' ? error.message : '加载失败，请检查网络';
        setState({ loading: false, error: message, blocked: delay > 0 });
        if (delay > 0) stopDelay = scheduleReadDelay(() => { if (current) setState((previous) => ({ ...previous, blocked: false })); }, delay);
      });
    return () => { current = false; controller.abort(); stopDelay?.(); };
  }, [bootstrap.rootPath, refresh]);
  const stats = state.data;
  return <div className="erp-page">
    <PageIntro eyebrow="COLLECTION ANALYTICS" title="了解采集链路的运行状况" description="查看请求趋势与分布，识别错误和异常变化。" />
    <div className="erp-toolbar"><Button isDisabled={state.blocked} onPress={() => setRefresh((value) => value + 1)}>刷新统计</Button>
    <p>最近 7 天，每日按 UTC 分组。今日请求是 UTC 今日总数，不受最近 7 天范围限制。</p></div>
    {state.loading && <p role="status">加载统计中…</p>}
    {state.error && <Alert status="danger"><Alert.Content><Alert.Title>统计不可用</Alert.Title><Alert.Description>{state.error}</Alert.Description><Button isDisabled={state.blocked} onPress={() => setRefresh((value) => value + 1)}>重试统计</Button></Alert.Content></Alert>}
    {stats && <>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{([
        ['总请求数', stats.total_requests], ['白名单命中', stats.whitelisted_requests], ['今日请求（UTC）', stats.today_requests], ['错误请求', stats.error_requests],
      ] as const).map(([label, value]) => <section key={label} aria-label={label} className="erp-metric"><h2>{label}</h2><p>{number(value)}</p></section>)}</div>
      <section className="erp-panel min-w-0 space-y-3"><h2>每日请求（UTC）</h2><div className="grid min-w-0 items-start gap-4 xl:grid-cols-2"><div className="min-w-0">{stats.daily.length > 0 ? <DailyChart rows={stats.daily} /> : <p>暂无每日数据</p>}</div>
        <Table variant="secondary" className="erp-table"><Table.ScrollContainer className="erp-table-scroll" role="region" aria-label="数据表格横向滚动区域" tabIndex={0}><Table.Content className="erp-table-content erp-compact-table" aria-label="每日请求（UTC）"><Table.Header className="erp-table-head"><Table.Column className="erp-table-column" isRowHeader>日期（UTC）</Table.Column><Table.Column className="erp-table-column">请求数</Table.Column></Table.Header>
          <Table.Body className="erp-table-body" renderEmptyState={() => <div className="erp-table-empty">{'暂无每日记录'}</div>}>{stats.daily.map((row) => <Table.Row className="erp-table-row" key={row.date} id={row.date}><Table.Cell className="erp-table-cell">{row.date}</Table.Cell><Table.Cell className="erp-table-cell">{number(row.count)}</Table.Cell></Table.Row>)}</Table.Body>
        </Table.Content></Table.ScrollContainer></Table>
        </div>
      </section>
      <div className="grid min-w-0 items-start gap-4 lg:grid-cols-3"><DistributionTable title="域名" rows={stats.by_host} /><DistributionTable title="方法" rows={stats.by_method} /><DistributionTable title="状态码" rows={stats.by_status} /></div>
    </>}
  </div>;
}
