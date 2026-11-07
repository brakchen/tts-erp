import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Alert, AlertDialog, Button, Chip, ListBox, Label, Select } from '@heroui/react';
import { ApiError, requestJson } from '../../shared/api/client';
import { parseIdentityJson } from '../../shared/api/identity-json';
import { pathFor } from '../../shared/api/paths';
import { withinRequestLifetime } from '../../shared/api/request-lifetime';
import type { FrontendBootstrap } from '../../shared/contracts';
import type { Asset, Count } from './contracts';
import './media-workspace.css';

export const mediaPageSize = 4;

export function mediaRequest<T>(root: string, path: string, decode: (data: unknown) => T, init?: RequestInit) {
  return requestJson<unknown>(root, '/v2/media' + path, init, parseIdentityJson)
    .then((response) => decode(response.data));
}

export function mediaWrite<T>(root: string, path: string, method: 'POST' | 'PATCH' | 'DELETE', decode: (data: unknown) => T, body?: object) {
  return withinRequestLifetime(new AbortController(), (signal) =>
    mediaRequest(root, path, decode, { method, signal, body: body === undefined ? undefined : JSON.stringify(body) }));
}

export function uncertainResult(error: unknown) {
  return !(error instanceof ApiError) || error.status >= 500 || (error.status >= 200 && error.status < 300);
}

export function mediaError(error: unknown, writing = false) {
  if (!(error instanceof ApiError) || (writing && error.status >= 500 && error.status !== 503)) {
    return writing ? '未能确认操作结果。请先核对当前记录，再决定是否重试。' : '暂时无法读取，请重试。';
  }
  if (error.status === 401) return '会话已失效，请重新登录。';
  if (error.status === 403) {
    if (error.detail === 'MEDIA_DELETE_PROTECTED' || error.code === 'MEDIA_DELETE_PROTECTED') {
      return '当前数据库受删除保护。请联系运维核对服务配置。';
    }
    return writing ? '你没有权限执行此操作。' : '你没有权限查看此内容。';
  }
  if (error.status === 404) return '记录不存在，或已被删除。';
  if (error.status === 429) return error.retryAfter ? '请求过于频繁，请至少等待 ' + error.retryAfter + ' 秒后重试。' : '请求过于频繁，请稍后重试。';
  if (error.status === 503) return '服务暂不可用，请稍后重试。';
  if (error.status === 400 || error.status === 422) return '资料未通过校验，请检查文件类型、大小或填写内容。';
  if (error.status === 409) {
    if (error.detail === 'ASSET_IN_USE' || error.code === 'ASSET_IN_USE') return '此素材仍被数字人引用。请先解除全部引用，再从素材库删除。';
    if (error.detail === 'ASSET_NOT_READY' || error.code === 'ASSET_NOT_READY') return '此资产还没有完成入库，暂时无法添加。';
    return '记录状态已变化，请重新查看后再操作。';
  }
  return '操作失败（HTTP ' + error.status + '），请重试。';
}

export function useMediaRead<T>(root: string, path: string | null, decode: (data: unknown) => T, revision: number | string = 0) {
  type State = { path: string | null; revision: number | string; attempt: number; data?: T; error?: string; loading: boolean };
  const [state, setState] = useState<State>({ path, revision, attempt: 0, loading: path !== null });
  const [attempt, setAttempt] = useState(0);
  const latestAttempt = useRef(0);
  useEffect(() => {
    const identity = { path, revision, attempt };
    if (path === null) {
      setState({ ...identity, loading: false });
      return;
    }
    const controller = new AbortController();
    let current = true;
    setState((previous) => ({ ...identity, loading: true,
      data: previous.path === path && previous.revision === revision ? previous.data : undefined }));
    void withinRequestLifetime(controller, (signal) => mediaRequest(root, path, decode, { signal }))
      .then((data) => { if (current && latestAttempt.current === attempt) setState({ ...identity, data, loading: false }); })
      .catch((error: unknown) => { if (current && latestAttempt.current === attempt) setState({ ...identity, error: mediaError(error), loading: false }); });
    return () => { current = false; controller.abort(); };
  }, [root, path, decode, revision, attempt]);
  return {
    ...(state.path === path && state.revision === revision && state.attempt === attempt
      ? state : { path, revision, attempt, loading: path !== null } as State),
    retry: () => {
      // Allocate this request's identity now. A prior response cannot acquire
      // a newer identity during the retry-to-effect window.
      const nextAttempt = ++latestAttempt.current;
      setState((previous) => ({ path, revision, attempt: nextAttempt, loading: path !== null,
        data: previous.path === path && previous.revision === revision ? previous.data : undefined }));
      setAttempt(nextAttempt);
    },
  };
}

export function useMediaAction() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const lock = useRef(false);
  async function run(operation: () => Promise<void>, propagate = false) {
    if (lock.current) {
      if (propagate) throw new Error('另一个操作尚未完成');
      return;
    }
    lock.current = true;
    setPending(true);
    setError('');
    setUncertain(false);
    try { await operation(); }
    catch (failure) {
      setError(mediaError(failure, true)); setUncertain(uncertainResult(failure));
      if (propagate) throw failure;
    }
    finally { lock.current = false; setPending(false); }
  }
  return { pending, error, uncertain, clearError: () => { setError(''); setUncertain(false); }, run };
}

export function useLeaveGuard(protect: boolean) {
  useEffect(() => {
    if (!protect) return;
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [protect]);
}

export function useRecordSelection(parameter: string) {
  const [selected, setSelected] = useState(() => {
    const value = new URLSearchParams(window.location.search).get(parameter);
    return value && /^[1-9]\d*$/.test(value) ? value : null;
  });
  function select(id: string | null) {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set(parameter, id);
    else url.searchParams.delete(parameter);
    window.history.replaceState(window.history.state, '', url);
    setSelected(id);
  }
  return [selected, select] as const;
}

export function recordLink(bootstrap: FrontendBootstrap, page: string, parameter?: string, id?: string) {
  const item = bootstrap.navigation.find((entry) => entry.id === page);
  if (!item) return undefined;
  const path = pathFor(bootstrap.rootPath, item.href);
  if (!parameter || !id) return path;
  const url = new URL(path, window.location.origin);
  url.searchParams.set(parameter, id);
  return url.pathname + url.search + url.hash;
}

export function ReadStatus({ title, loading, error, onRetry }: {
  title: string; loading: boolean; error?: string; onRetry: () => void;
}) {
  if (error) return <Alert role="alert" status="danger" className="media-error shadow-none"><Alert.Content>
    <Alert.Title>{title}不可用</Alert.Title><Alert.Description className="text-inherit [overflow-wrap:anywhere]">{error}</Alert.Description>
    <Button variant="secondary" onPress={onRetry}>重试{title}</Button>
  </Alert.Content></Alert>;
  if (loading) return <p role="status">正在加载{title}…</p>;
  return null;
}

export function ActionError({ message }: { message: string }) {
  return message ? <Alert role="alert" status="danger" className="media-error shadow-none"><Alert.Content>
    <Alert.Description className="text-inherit [overflow-wrap:anywhere]">{message}</Alert.Description>
  </Alert.Content></Alert> : null;
}

export function RecordPage({ eyebrow, title, description, actions, pending = false, onClose, children, backLabel = '返回列表' }: {
  eyebrow: string; title: string; description?: ReactNode; actions?: ReactNode;
  pending?: boolean; onClose?: () => void; children: ReactNode; backLabel?: string;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [title]);
  return <section className="media-record">
    <header className="media-header" data-media-header>
      <div className="media-title"><p className="media-caption">{eyebrow}</p>
        <h1 ref={heading} tabIndex={-1}>{title}</h1>
        {description && <div className="media-description">{description}</div>}
      </div>
      {(actions || onClose) && <div className="media-actions">{actions}
        {onClose && <Button variant="ghost" className="media-back" isDisabled={pending} onPress={onClose}>{backLabel}</Button>}
      </div>}
    </header>
    {children}
  </section>;
}

export function DiscardDialog({ open, description, onKeep, onDiscard }: {
  open: boolean; description: string; onKeep: () => void; onDiscard: () => void;
}) {
  return <AlertDialog.Backdrop isOpen={open} onOpenChange={(next) => { if (!next) onKeep(); }}>
    <AlertDialog.Container><AlertDialog.Dialog>
      <AlertDialog.Header><AlertDialog.Heading>放弃本次操作？</AlertDialog.Heading></AlertDialog.Header>
      <AlertDialog.Body><p>{description}</p></AlertDialog.Body>
      <AlertDialog.Footer>
        <Button variant="secondary" autoFocus onPress={onKeep}>继续处理</Button>
        <Button variant="danger" onPress={onDiscard}>确认放弃</Button>
      </AlertDialog.Footer>
    </AlertDialog.Dialog></AlertDialog.Container>
  </AlertDialog.Backdrop>;
}

export function MediaSelect({ label, value, options, onChange, disabled = false }: {
  label: string; value: string; options: readonly { value: string; label: string }[]; onChange: (value: string) => void; disabled?: boolean;
}) {
  // Index keys keep every literal tag (including "all") selectable.
  const selectedKey = String(options.findIndex((option) => option.value === value));
  return <Select isDisabled={disabled} selectedKey={selectedKey} onSelectionChange={(key) => onChange(options[Number(key)]?.value ?? '')}>
    <Label>{label}</Label>
    <Select.Trigger className="media-select-trigger"><Select.Value /><Select.Indicator /></Select.Trigger>
    <Select.Popover><ListBox>{options.map((item, index) =>
      <ListBox.Item key={index} id={String(index)} textValue={item.label}>{item.label}</ListBox.Item>)}
    </ListBox></Select.Popover>
  </Select>;
}

export function MediaPagination({ name, offset, limit, total, hasNext, loading, onChange }: {
  name: string; offset: number; limit: number; total?: Count; hasNext: boolean; loading: boolean; onChange: (offset: number) => void;
}) {
  // Persona lists return an array without a total; retain access to their next
  // server page without inventing a count. Asset consumers always supply total.
  if (total === undefined && !offset && !hasNext) return null;
  return <nav aria-label={name} className="media-pagination">
    <span>{total === undefined ? '' : `共 ${total} 项 · `}第 {Math.floor(offset / limit) + 1} 页</span>
    <div className="media-actions">
      <Button variant="secondary" isDisabled={!offset || loading} onPress={() => onChange(Math.max(0, offset - limit))}>上一页</Button>
      <Button variant="secondary" isDisabled={!hasNext || loading} onPress={() => onChange(offset + limit)}>下一页</Button>
    </div>
  </nav>;
}

export function humanStatus(status: string) {
  if (status === 'active') return '启用中';
  if (status === 'archived') return '已归档';
  return '状态未知';
}

export function assetType(asset: Asset) {
  return asset.kind === 'video' ? '视频' : '图片';
}

export function assetStatus(status: string) {
  if (status === 'ready') return '已入库';
  if (status === 'awaiting_upload') return '待入库';
  if (status === 'failed') return '入库失败';
  return '状态未知';
}

export function AssetTags({ tags }: { tags: readonly string[] }) {
  return tags.length ? <div className="media-tags" role="list" aria-label="素材标签">{tags.map((tag) =>
    <Chip key={tag} size="sm" className="media-tag" role="listitem"><Chip.Label className="[overflow-wrap:anywhere] whitespace-normal">{tag}</Chip.Label></Chip>)}</div>
    : <span className="media-muted">未设置标签</span>;
}
