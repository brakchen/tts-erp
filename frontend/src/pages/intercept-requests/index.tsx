import { useEffect, useState } from 'react';
import { Alert, Button, Input, Label, ListBox, Modal, Select, Table, TextField } from '@heroui/react';
import { ApiError, requestJson } from '../../shared/api/client';
import type { PageProps } from '../../shared/contracts';

// These read endpoints return database-named fields, including a string request_id.
interface InterceptRequest {
  request_id: string;
  method: string;
  endpoint_host: string;
  endpoint_path: string;
  captured_at: string | null;
  response_status: number | null;
  duration_ms: number | null;
  is_whitelisted: boolean;
  url?: string | null;
  matched_config_id?: number | null;
  error_type?: string | null;
  error_message?: string | null;
  request_headers?: unknown;
  request_body?: unknown;
  response_headers?: unknown;
  response_body?: unknown;
}
interface RequestList { requests: InterceptRequest[]; total: number; limit: number; offset: number }
type ReadState<T> = { data?: T; error?: string; loading: boolean; retryBlocked?: boolean };
const pageSize = 100;
const emptyFilters = { endpoint_host: '', endpoint_path: '', status: '', is_whitelisted: '', method: '', from: '', to: '' };
type Filters = typeof emptyFilters;
function errorMessage(error: unknown) {
  if (error instanceof ApiError) return `${error.message} (${error.status})${error.requestId ? ` · 请求 ${error.requestId}` : ''}${error.retryAfter ? ` · 请在 ${error.retryAfter} 秒后重试` : ''}`;
  return error instanceof Error ? error.message : '加载失败，请检查网络';
}
function formatTime(value: string | null | undefined) {
  if (!value || Number.isNaN(Date.parse(value))) return '—';
  return new Date(value).toLocaleString();
}
function JsonSection({ title, value }: { title: string; value: unknown }) {
  return <section><h3>{title}</h3><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all">{value == null ? '—' : JSON.stringify(value, null, 2)}</pre></section>;
}

export default function InterceptRequests({ bootstrap }: PageProps) {
  const [draft, setDraft] = useState<Filters>(emptyFilters);
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [list, setList] = useState<ReadState<RequestList>>({ loading: true });
  const [selected, setSelected] = useState<string | null>(null);
  const [detailRefresh, setDetailRefresh] = useState(0);
  const [detail, setDetail] = useState<ReadState<InterceptRequest>>({ loading: true });

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    let cooldownTimer: ReturnType<typeof setTimeout> | undefined;
    setList({ loading: true });
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
    params.set('limit', String(pageSize));
    params.set('offset', String(offset));
    requestJson<RequestList>(bootstrap.rootPath, `/v2/intercept/requests?${params}`, { signal: controller.signal })
      .then(({ data }) => {
        if (!data || !Array.isArray(data.requests) || typeof data.total !== 'number' || !data.requests.every((item) => typeof item.request_id === 'string')) throw new Error('拦截记录响应格式错误');
        if (current) setList({ loading: false, data });
      })
      .catch((error: unknown) => {
        if (!current) return;
        const delay = error instanceof ApiError ? (error.retryAfter ?? 0) * 1000 : 0;
        setList({ loading: false, error: errorMessage(error), retryBlocked: delay > 0 });
        if (delay > 0) cooldownTimer = setTimeout(() => { if (current) setList((state) => ({ ...state, retryBlocked: false })); }, delay);
      });
    return () => { current = false; controller.abort(); clearTimeout(cooldownTimer); };
  }, [bootstrap.rootPath, filters, offset, refresh]);

  useEffect(() => {
    if (selected === null) return;
    const controller = new AbortController();
    let current = true;
    let cooldownTimer: ReturnType<typeof setTimeout> | undefined;
    setDetail({ loading: true });
    requestJson<{ request: InterceptRequest }>(bootstrap.rootPath, `/v2/intercept/requests/${encodeURIComponent(selected)}`, { signal: controller.signal })
      .then(({ data }) => {
        if (!data?.request || data.request.request_id !== selected) throw new Error('请求详情响应格式错误');
        if (current) setDetail({ loading: false, data: data.request });
      })
      .catch((error: unknown) => {
        if (!current) return;
        const delay = error instanceof ApiError ? (error.retryAfter ?? 0) * 1000 : 0;
        setDetail({ loading: false, error: errorMessage(error), retryBlocked: delay > 0 });
        if (delay > 0) cooldownTimer = setTimeout(() => { if (current) setDetail((state) => ({ ...state, retryBlocked: false })); }, delay);
      });
    return () => { current = false; controller.abort(); clearTimeout(cooldownTimer); };
  }, [bootstrap.rootPath, selected, detailRefresh]);

  const total = list.data?.total;
  const request = detail.data;
  return <div className="space-y-4">
    <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => {
      event.preventDefault();
      if (list.retryBlocked) return;
      setFilters(Object.fromEntries(Object.entries(draft).map(([key, value]) => [key, value.trim()])) as Filters);
      setOffset(0);
      setRefresh((value) => value + 1);
      setSelected(null);
    }}>
      {([{ key: 'endpoint_host', label: '域名' }, { key: 'endpoint_path', label: '路径' }, { key: 'status', label: '状态码' },
        { key: 'from', label: '起始', type: 'date' }, { key: 'to', label: '截止', type: 'date' }] as const).map((field) =>
        <TextField key={field.key} value={draft[field.key]} onChange={(value) => setDraft((previous) => ({ ...previous, [field.key]: value }))}>
          <Label>{field.label}</Label><Input type={'type' in field ? field.type : 'text'} />
        </TextField>)}
      <Select selectedKey={draft.is_whitelisted || 'all'} onSelectionChange={(key) => setDraft((previous) => ({ ...previous, is_whitelisted: key === 'all' ? '' : String(key) }))}>
        <Label>白名单</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
        <Select.Popover><ListBox><ListBox.Item id="all" textValue="全部">全部</ListBox.Item><ListBox.Item id="true" textValue="是">是</ListBox.Item><ListBox.Item id="false" textValue="否">否</ListBox.Item></ListBox></Select.Popover>
      </Select>
      <Select selectedKey={draft.method || 'all'} onSelectionChange={(key) => setDraft((previous) => ({ ...previous, method: key === 'all' ? '' : String(key) }))}>
        <Label>方法</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
        <Select.Popover><ListBox>{['all', 'GET', 'POST', 'PUT', 'DELETE'].map((method) => <ListBox.Item key={method} id={method} textValue={method === 'all' ? '全部' : method}>{method === 'all' ? '全部' : method}</ListBox.Item>)}</ListBox></Select.Popover>
      </Select>
      <Button type="submit" isDisabled={list.retryBlocked}>查询</Button>
    </form>
    <p aria-live="polite">{total == null ? '共 — 条' : `共 ${total} 条`}</p>
    {list.loading && <p role="status">加载中…</p>}
    {list.error && <Alert status="danger"><Alert.Content><Alert.Title>加载记录失败</Alert.Title><Alert.Description>{list.error}</Alert.Description><Button isDisabled={list.retryBlocked} onPress={() => setRefresh((value) => value + 1)}>重试列表</Button></Alert.Content></Alert>}
    {list.data && <Table><Table.ScrollContainer><Table.Content aria-label="拦截记录">
      <Table.Header>{['时间', '方法', '域名', '路径', '状态', '耗时', '白名单', '详情'].map((label, index) => <Table.Column key={label} isRowHeader={index === 0}>{label}</Table.Column>)}</Table.Header>
      <Table.Body renderEmptyState={() => '暂无记录'}>{list.data.requests.map((item) => <Table.Row key={item.request_id} id={item.request_id} onAction={() => { setDetail({ loading: true }); setSelected(item.request_id); }}>
        <Table.Cell>{formatTime(item.captured_at)}</Table.Cell><Table.Cell>{item.method}</Table.Cell>
        <Table.Cell><span className="break-all">{item.endpoint_host}</span></Table.Cell><Table.Cell><span className="break-all">{item.endpoint_path}</span></Table.Cell>
        <Table.Cell>{item.response_status ?? '—'}</Table.Cell><Table.Cell>{item.duration_ms == null ? '—' : `${item.duration_ms}ms`}</Table.Cell>
        <Table.Cell>{item.is_whitelisted ? '是' : '否'}</Table.Cell><Table.Cell focusMode="child"><Button size="sm" onPress={() => { setDetail({ loading: true }); setSelected(item.request_id); }}>查看详情</Button></Table.Cell>
      </Table.Row>)}</Table.Body>
    </Table.Content></Table.ScrollContainer></Table>}
    <div className="flex flex-wrap items-center gap-4">
      <Button isDisabled={list.loading || list.retryBlocked || offset === 0} onPress={() => setOffset(Math.max(0, offset - pageSize))}>上一页</Button>
      <span>{total == null ? '—' : `${Math.floor(offset / pageSize) + 1} / ${Math.max(1, Math.ceil(total / pageSize))}`}</span>
      <Button isDisabled={list.loading || list.retryBlocked || total == null || offset + pageSize >= total} onPress={() => setOffset(offset + pageSize)}>下一页</Button>
    </div>
    <Modal.Backdrop isOpen={selected !== null} onOpenChange={(open) => { if (!open) setSelected(null); }}><Modal.Container size="lg" scroll="inside"><Modal.Dialog>
        <Modal.CloseTrigger aria-label="关闭详情" />
        <Modal.Header><Modal.Heading>请求详情</Modal.Heading></Modal.Header>
        <Modal.Body className="space-y-4">
          {detail.loading && <p role="status">加载详情中…</p>}
          {detail.error && <Alert status="danger"><Alert.Content><Alert.Title>加载详情失败</Alert.Title><Alert.Description>{detail.error}</Alert.Description><Button isDisabled={detail.retryBlocked} onPress={() => setDetailRefresh((value) => value + 1)}>重试详情</Button></Alert.Content></Alert>}
          {request && <>
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-3 break-all">
              <dt>请求 ID</dt><dd>{request.request_id}</dd><dt>方法</dt><dd>{request.method}</dd>
              <dt>URL</dt><dd>{request.url ?? '—'}</dd><dt>域名</dt><dd>{request.endpoint_host}</dd><dt>路径</dt><dd>{request.endpoint_path}</dd>
              <dt>白名单</dt><dd>{request.is_whitelisted ? `是 (配置 #${request.matched_config_id ?? '—'})` : '否'}</dd>
              <dt>耗时</dt><dd>{request.duration_ms == null ? '—' : `${request.duration_ms}ms`}</dd><dt>状态码</dt><dd>{request.response_status ?? '—'}</dd>
              <dt>时间</dt><dd>{formatTime(request.captured_at)}</dd>
              {request.error_type && <><dt>错误类型</dt><dd>{request.error_type}</dd></>}{request.error_message && <><dt>错误信息</dt><dd>{request.error_message}</dd></>}
            </dl>
            <JsonSection title="请求头" value={request.request_headers} /><JsonSection title="请求体" value={request.request_body} />
            <JsonSection title="响应头" value={request.response_headers} /><JsonSection title="响应体" value={request.response_body} />
          </>}
        </Modal.Body>
      </Modal.Dialog></Modal.Container></Modal.Backdrop>
  </div>;
}
