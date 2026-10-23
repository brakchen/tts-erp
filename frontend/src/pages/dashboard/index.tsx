import { useEffect, useState } from 'react';
import { Alert, Button, Card, Link, Table } from '@heroui/react';
import { ApiError, requestJson } from '../../shared/api/client';
import { pathFor } from '../../shared/api/paths';
import type { NavigationItem, PageProps } from '../../shared/contracts';

interface Shop { id: number; shop_id: string; account_name: string | null; region: string | null; platform: string }
interface ShopList { items: Shop[]; total: number | null }
interface Coverage { total_spus: number | null; missing_cost_spus: number | null }
interface Health { status: string; auth_mode?: string }
interface SyncJob {
  job_name: string;
  interval_seconds: number | null;
  last_run_at: string | null;
  next_expected_at: string | null;
  last_status: string | null;
  last_error: string | null;
  cycles_late: number | null;
  severity: string;
}
interface SyncStatus { server_time: string; jobs: SyncJob[] }
interface ReadState<T> { data?: T; loading: boolean; error?: string; retryBlocked?: boolean }
type Decoder<T> = (value: unknown, headers: Headers) => T;
function isRecord(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function countOrUnknown(value: unknown) { return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null; }
const decodeShops: Decoder<ShopList> = (value, headers) => {
  if (!Array.isArray(value) || !value.every((shop: unknown) => isRecord(shop) && typeof shop.shop_id === 'string')) throw new Error('店铺响应格式错误');
  const total = headers.get('X-Total-Count');
  return { items: value, total: total !== null && /^\d+$/.test(total) ? countOrUnknown(Number(total)) : null };
};
const decodeCoverage: Decoder<Coverage> = (value) => {
  if (!isRecord(value)) throw new Error('覆盖率响应格式错误');
  return { total_spus: countOrUnknown(value.total_spus), missing_cost_spus: countOrUnknown(value.missing_cost_spus) };
};
const decodeHealth: Decoder<Health> = (value) => {
  if (!isRecord(value) || typeof value.status !== 'string') throw new Error('健康检查响应格式错误');
  return { status: value.status, auth_mode: typeof value.auth_mode === 'string' ? value.auth_mode : undefined };
};
const decodeSync: Decoder<SyncStatus> = (value) => {
  if (!isRecord(value) || typeof value.server_time !== 'string' || !Array.isArray(value.jobs) || !value.jobs.every((job: unknown) => isRecord(job) && typeof job.job_name === 'string')) throw new Error('同步状态响应格式错误');
  return { server_time: value.server_time, jobs: value.jobs };
};

function useDashboardRead<T>(rootPath: string, path: string, decode: Decoder<T>, refreshInterval?: number) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<ReadState<T>>({ loading: true });
  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    let cooldownTimer: ReturnType<typeof setTimeout> | undefined;
    let refreshTimer = refreshInterval ? setTimeout(() => setAttempt((value) => value + 1), refreshInterval) : undefined;
    setState({ loading: true });
    requestJson<unknown>(rootPath, path, { signal: controller.signal })
      .then(({ data, headers }) => {
        const decoded = decode(data, headers);
        if (current) setState({ data: decoded, loading: false });
      })
      .catch((error: unknown) => {
        if (!current) return;
        const message = error instanceof ApiError
          ? `${error.message} (${error.status})${error.requestId ? ` · 请求 ${error.requestId}` : ''}${error.retryAfter ? ` · 请在 ${error.retryAfter} 秒后重试` : ''}`
          : error instanceof Error ? error.message : '加载失败，请检查网络';
        const delay = error instanceof ApiError ? (error.retryAfter ?? 0) * 1000 : 0;
        setState({ loading: false, error: message, retryBlocked: delay > 0 });
        if (delay > 0) {
          cooldownTimer = setTimeout(() => { if (current) setState((value) => ({ ...value, retryBlocked: false })); }, delay);
          if (refreshInterval) {
            clearTimeout(refreshTimer);
            refreshTimer = setTimeout(() => setAttempt((value) => value + 1), Math.max(refreshInterval, delay));
          }
        }
      });
    return () => { current = false; controller.abort(); clearTimeout(cooldownTimer); clearTimeout(refreshTimer); };
  }, [rootPath, path, decode, attempt, refreshInterval]);
  return { ...state, retry: () => { if (!state.retryBlocked) setAttempt((value) => value + 1); } };
}
function ReadFeedback({ state, label }: { state: ReadState<unknown> & { retry: () => void }; label: string }) {
  return <>{state.loading && <p role="status">{label}加载中…</p>}{state.error && <Alert status="danger"><Alert.Content><Alert.Title>{label}加载失败</Alert.Title><Alert.Description>{state.error}</Alert.Description><Button isDisabled={state.retryBlocked} onPress={state.retry}>重试{label}</Button></Alert.Content></Alert>}</>;
}
function formatTime(value: string | null | undefined) {
  return !value || Number.isNaN(Date.parse(value)) ? '—' : new Date(value).toLocaleString();
}
function formatInterval(seconds: number | null) {
  if (seconds == null) return '—';
  if (seconds && seconds % 3600 === 0) return `${seconds / 3600} 小时`;
  if (seconds && seconds % 60 === 0) return `${seconds / 60} 分钟`;
  return `${seconds} 秒`;
}
const severityLabels: Record<string, string> = { crit: '红灯：落后 ≥2 个周期', warn: '落后 1 个周期', ok: '正常（一个周期内）', unknown: '未知（从未运行或无周期配置）' };
const severityRank: Record<string, number> = { crit: 0, warn: 1, ok: 2, unknown: 3 };
const statusLabels: Record<string, string> = { succeeded: '成功', failed: '失败', running: '运行中…' };
const quickPageIds = new Set(['manual-costs', 'spu-roi', 'shops', 'intercept-configs', 'runtime-configs', 'ad-daily']);

export default function Dashboard({ bootstrap }: PageProps) {
  const shops = useDashboardRead(bootstrap.rootPath, '/v2/commerce/channel-accounts?limit=500', decodeShops);
  const coverage = useDashboardRead(bootstrap.rootPath, '/v2/reporting/coverage', decodeCoverage);
  const health = useDashboardRead(bootstrap.rootPath, '/healthz', decodeHealth);
  const sync = useDashboardRead(bootstrap.rootPath, '/v2/sync/status', decodeSync, 60_000);
  const navigation = (id: string) => bootstrap.navigation.find((item) => item.id === id);
  const href = (item: NavigationItem) => pathFor(bootstrap.rootPath, item.href);
  const total = shops.data?.total;
  const scope = total === 0 ? '无店铺' : total === 1 && shops.data?.items.length === 1
    ? `${shops.data.items[0].account_name || '(未命名)'} · ${shops.data.items[0].region || '?'}`
    : total == null ? '全店铺（总数未知）' : `全店铺 (${total})`;
  const metrics = [
    { label: '待录成本', value: coverage.data?.missing_cost_spus, hint: '缺少采购成本的 SPU 数量', pageId: 'manual-costs' },
    { label: 'SPU 总数', value: coverage.data?.total_spus, hint: '已跟踪的 SPU 数量', pageId: 'spu-roi' },
    { label: '已注册店铺总数', value: total, hint: '已登记的店铺数量', pageId: 'shops' },
  ];
  const sortedJobs = sync.data?.jobs.slice().sort((a, b) => (severityRank[a.severity] ?? 9) - (severityRank[b.severity] ?? 9) || a.job_name.localeCompare(b.job_name));
  return <div className="space-y-6">
    <Card><Card.Header><Card.Title>快速导航</Card.Title></Card.Header><Card.Content className="flex flex-wrap gap-4">
      {bootstrap.navigation.filter((item) => quickPageIds.has(item.id)).map((item) => <Link key={item.id} href={href(item)}>{item.label}</Link>)}
    </Card.Content></Card>
    <Card><Card.Header><Card.Title>数据摘要</Card.Title><Card.Description>{scope}</Card.Description></Card.Header><Card.Content>
      <ReadFeedback state={coverage} label="数据摘要" />
      <div className="grid gap-4 sm:grid-cols-3">{metrics.map((metric) => {
        const target = navigation(metric.pageId);
        return <Card key={metric.label} role="region" aria-label={metric.label}><Card.Header><Card.Title>{metric.label}</Card.Title></Card.Header><Card.Content><p>{metric.value ?? '—'}</p><p>{metric.hint}</p>{target && <Link href={href(target)}>{target.label}</Link>}</Card.Content></Card>;
      })}</div>
      {navigation('enum-map') && <Link href={href(navigation('enum-map')!)}>枚举映射管理</Link>}
    </Card.Content></Card>
    <Card><Card.Header><Card.Title>已注册店铺</Card.Title>{navigation('shops') && <Link href={href(navigation('shops')!)}>管理店铺</Link>}</Card.Header><Card.Content>
      <ReadFeedback state={shops} label="店铺" />
      {shops.data && (shops.data.items.length === 0 ? <p>暂无店铺</p> : <ul className="space-y-3">{shops.data.items.map((shop) => <li key={shop.id} className="flex flex-wrap gap-3 break-all">
        <span>{shop.account_name || '(未命名)'}</span><span>{shop.shop_id}</span><span>{shop.region || '?'}</span><span>{shop.platform}</span>
      </li>)}</ul>)}
    </Card.Content></Card>
    <Card><Card.Header><Card.Title>系统状态</Card.Title></Card.Header><Card.Content className="space-y-3">
      <ReadFeedback state={health} label="API" />
      {health.data && <p>{health.data.status === 'ok' ? 'API 正常' : `API 异常 (${health.data.status})`} <span>auth: {health.data.auth_mode ?? '?'}</span></p>}
      {['数据库 · PostgreSQL', '存储 · MinIO', '同步服务 · APScheduler'].map((label) => <div key={label}><span>{label}</span><p>健康状态未知（接口未提供）</p></div>)}
    </Card.Content></Card>
    <Card><Card.Header><Card.Title>数据同步状态</Card.Title><Card.Description>{sync.data ? `刷新于 ${formatTime(sync.data.server_time)}` : '—'}</Card.Description></Card.Header><Card.Content>
      <ReadFeedback state={sync} label="同步状态" />
      {sortedJobs && <Table><Table.ScrollContainer><Table.Content aria-label="数据同步状态">
        <Table.Header>{['健康', '作业', '周期', '最近一次同步', '预计下次同步', '状态'].map((label) => <Table.Column key={label} isRowHeader={label === '作业'}>{label}</Table.Column>)}</Table.Header>
        <Table.Body renderEmptyState={() => '暂无周期作业'}>{sortedJobs.map((job) => <Table.Row key={job.job_name} id={job.job_name}>
          <Table.Cell>{severityLabels[job.severity] ?? severityLabels.unknown}</Table.Cell><Table.Cell>{job.job_name}</Table.Cell><Table.Cell>{formatInterval(job.interval_seconds)}</Table.Cell>
          <Table.Cell>{formatTime(job.last_run_at)}</Table.Cell><Table.Cell>{formatTime(job.next_expected_at)}</Table.Cell><Table.Cell>
            <span>{job.last_status == null ? '从未运行' : statusLabels[job.last_status] ?? job.last_status}</span>
            {(job.severity === 'crit' || job.severity === 'warn') && job.cycles_late != null && <p>落后 {job.cycles_late.toFixed(1)} 周期</p>}
            {job.last_error && <p className="break-all">{job.last_error}</p>}
          </Table.Cell>
        </Table.Row>)}</Table.Body>
      </Table.Content></Table.ScrollContainer></Table>}
    </Card.Content></Card>
  </div>;
}
