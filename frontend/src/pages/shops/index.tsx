import OffsetPagination from '../../shared/OffsetPagination';
import PageIntro from '../../shared/PageIntro';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Chip, Input, Label, ListBox, Modal, Select, Table, TextField, buttonVariants } from '@heroui/react';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { commandFailure, type CommandFailure } from '../../shared/api/command-errors';
import type { PageProps } from '../../shared/contracts';
import { decodeAuthorization, decodeShop, decodeShopCommand, decodeShopList, decodeTotal, type AuthorizationLink, type Shop } from './contracts';
import ShopForm, { fieldLabels, type ShopDraft, type ShopFormKind } from './ShopForm';
import './shops.css';

interface Attempt { key: string; kind: ShopFormKind | 'authorize'; id?: number; shop_id: string; appPair: boolean }
interface Recovery { attempt: Attempt; phase: 'unverified' | 'checking' | 'checked' | 'failed'; snapshot?: Shop; message?: string }
interface Filters { q: string; source: string; app_state: string; auth_state: string }
const initialFilters: Filters = { q: '', source: '', app_state: '', auth_state: '' };
const pageSize = 50;
const shopKey = (shop?: Shop) => shop ? String(shop.id) : 'register';
const authLabels = { unauthorized: '未授权', authorized: '已授权', reauthorize: '需重新授权', unknown: '待确认' };
const sourceLabels: Record<string, string> = { 'ads-data-sync': 'Ads', monitor: 'Monitor' };
const filterOptions = [
  { key: 'source', label: '发现来源', options: [['all', '全部来源'], ['ads-data-sync', 'Ads 插件'], ['monitor', 'Monitor 插件']] },
  { key: 'app_state', label: 'App 配置', options: [['all', '全部配置'], ['configured', '已配置'], ['unconfigured', '未配置']] },
  { key: 'auth_state', label: 'OAuth 授权', options: [['all', '全部授权状态'], ...Object.entries(authLabels)] },
] as const;
function localTime(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN') : '未上报';
}
function listPath(filters: Filters, offset: number) {
  const query = new URLSearchParams({ platform: 'tiktok', view: 'management', limit: String(pageSize), offset: String(offset) });
  for (const [key, value] of Object.entries(filters)) if (value.trim()) query.set(key, value.trim());
  return '/v2/commerce/channel-accounts?' + query;
}

export default function Shops({ bootstrap }: PageProps) {
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const [filters, setFilters] = useState(initialFilters);
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [list, setList] = useState<{ loading: boolean; shops?: Shop[]; total?: number | null; error?: string }>({ loading: true });
  const [readBlocked, setReadBlocked] = useState(false);
  const [dialog, setDialog] = useState<{ kind: ShopFormKind; shop?: Shop }>();
  const [discardOpen, setDiscardOpen] = useState(false);
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const [cooldown, setCooldown] = useState<Record<string, boolean>>({});
  const [failures, setFailures] = useState<Record<string, CommandFailure | undefined>>({});
  const [recoveries, setRecoveries] = useState<Record<string, Recovery | undefined>>({});
  const [notice, setNotice] = useState('');
  const [link, setLink] = useState<{ shop: Shop; value: AuthorizationLink }>();
  const active = useRef(new Set<string>());
  const mounted = useRef(true);
  const dirty = useRef(false);
  const readNotBefore = useRef(0);
  const delays = useRef<(() => void)[]>([]);
  const blockReads = useCallback((milliseconds: number) => {
    if (milliseconds <= 0) return;
    readNotBefore.current = Math.max(readNotBefore.current, performance.now() + milliseconds);
    setReadBlocked(true);
    delays.current.push(scheduleReadDelay(() => {
      if (mounted.current && performance.now() >= readNotBefore.current) setReadBlocked(false);
    }, milliseconds));
  }, []);
  useEffect(() => {
    mounted.current = true;
    const focus = () => setRefresh((value) => value + 1);
    window.addEventListener('focus', focus);
    return () => { mounted.current = false; delays.current.forEach((cancel) => cancel()); window.removeEventListener('focus', focus); };
  }, []);
  useEffect(() => {
    if (performance.now() < readNotBefore.current) return;
    const controller = new AbortController();
    setList((previous) => ({ ...previous, loading: true, error: undefined }));
    requestJson<unknown>(bootstrap.rootPath, listPath(filters, offset), { signal: controller.signal })
      .then(({ data, headers }) => {
        if (!controller.signal.aborted) setList({ loading: false, shops: decodeShopList(data), total: decodeTotal(headers.get('X-Total-Count')) });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setList((previous) => ({ ...previous, loading: false, error: error instanceof ApiError ? `读取店铺失败（HTTP ${error.status}）` : '店铺列表暂不可用，请重试' }));
        if (error instanceof ApiError) blockReads((error.retryAfter ?? 0) * 1000);
      });
    return () => controller.abort();
  }, [bootstrap.rootPath, filters, offset, refresh, blockReads]);
  function blocked(key: string) {
    return !canWrite || pending[key] || cooldown[key] || (recoveries[key] !== undefined && recoveries[key]?.phase !== 'checked');
  }
  function begin(key: string) {
    if (blocked(key) || active.current.has(key)) return false;
    active.current.add(key);
    setPending((previous) => ({ ...previous, [key]: true }));
    setFailures((previous) => ({ ...previous, [key]: undefined }));
    setRecoveries((previous) => ({ ...previous, [key]: undefined }));
    setNotice('');
    return true;
  }
  function end(key: string) {
    active.current.delete(key);
    if (mounted.current) setPending((previous) => ({ ...previous, [key]: false }));
  }
  function reject(error: unknown, attempt: Attempt) {
    if (!mounted.current) return;
    const failure = commandFailure(error, fieldLabels);
    setFailures((previous) => ({ ...previous, [attempt.key]: failure }));
    if (failure.uncertain) setRecoveries((previous) => ({ ...previous, [attempt.key]: { attempt, phase: 'unverified' } }));
    if (failure.retryAfter > 0) {
      setCooldown((previous) => ({ ...previous, [attempt.key]: true }));
      delays.current.push(scheduleReadDelay(() => {
        if (mounted.current) setCooldown((previous) => ({ ...previous, [attempt.key]: false }));
      }, failure.retryAfter * 1000));
    }
  }
  function open(kind: ShopFormKind, shop?: Shop) {
    const key = shopKey(shop);
    if (blocked(key)) return;
    dirty.current = false;
    setFailures((previous) => ({ ...previous, [key]: undefined }));
    setDialog({ kind, shop });
  }
  function close() {
    if (dialog && pending[shopKey(dialog.shop)]) return;
    if (dirty.current) { setDiscardOpen(true); return; }
    dirty.current = false; setDialog(undefined);
  }
  async function save(draft: ShopDraft) {
    if (!dialog) return;
    const target = dialog;
    const key = shopKey(target.shop);
    const appKey = draft.app_key.trim();
    const secret = draft.app_secret;
    if (target.kind === 'app' && (Boolean(appKey) !== Boolean(secret))) {
      setFailures((previous) => ({ ...previous, [key]: { message: '请成对填写 App Key 和 App Secret', fieldErrors: { app_key: '请成对填写', app_secret: '请成对填写' }, uncertain: false, retryAfter: 0 } })); return;
    }
    if (!begin(key)) return;
    const register = target.kind === 'register';
    const body: Record<string, string | null> = register
      ? { platform: 'tiktok', shop_id: draft.shop_id.trim() } : {};
    if (register) for (const field of ['account_name', 'region', 'opened_date'] as const) {
      if (draft[field].trim()) body[field] = draft[field].trim();
    }
    if (target.kind === 'edit') body.opened_date = draft.opened_date || null;
    if (target.kind === 'app') {
      body.service_id = draft.service_id.trim();
      if (appKey && secret) { body.app_key = appKey; body.app_secret = secret; }
    }
    try {
      const { data } = await requestJson<unknown>(bootstrap.rootPath, register ? '/v2/admin/shops/register' : `/v2/admin/shops/${target.shop!.id}`, { method: register ? 'POST' : 'PATCH', body: JSON.stringify(body) });
      decodeShopCommand(data, register ? { shop_id: String(body.shop_id), register: true } : { id: target.shop!.id });
      if (mounted.current) {
        dirty.current = false; setDialog(undefined);
        setNotice(target.kind === 'app' ? '当前店铺的 App 配置已保存，可以获取授权链接。' : '店铺信息已保存');
        setRefresh((value) => value + 1);
      }
    } catch (error: unknown) {
      reject(error, { key, kind: target.kind, id: target.shop?.id, shop_id: register ? String(body.shop_id) : target.shop!.shop_id, appPair: Boolean(appKey && secret) });
    } finally { end(key); }
  }
  async function authorize(shop: Shop) {
    const key = shopKey(shop);
    if (!begin(key)) return;
    try {
      const params = new URLSearchParams({ format: 'json', shop_pk: String(shop.id) });
      const { data } = await requestJson<unknown>(bootstrap.rootPath, '/v2/oauth/tiktok/authorize?' + params);
      const value = decodeAuthorization(data);
      if (mounted.current) setLink({ shop, value });
    } catch (error: unknown) { reject(error, { key, kind: 'authorize', id: shop.id, shop_id: shop.shop_id, appPair: false }); }
    finally { end(key); }
  }
  async function verify(key: string) {
    const recovery = recoveries[key];
    if (!recovery || recovery.phase === 'checking' || readBlocked) return;
    const attempt = recovery.attempt;
    setRecoveries((previous) => ({ ...previous, [key]: { attempt, phase: 'checking' } }));
    try {
      const path = attempt.id === undefined ? listPath({ ...initialFilters, q: attempt.shop_id }, 0) : `/v2/commerce/channel-accounts/${attempt.id}?view=management`;
      const { data } = await requestJson<unknown>(bootstrap.rootPath, path);
      const snapshot = attempt.id === undefined ? decodeShopList(data).find((shop) => shop.shop_id === attempt.shop_id) : decodeShop(data);
      if (attempt.id !== undefined && (snapshot?.id !== attempt.id || snapshot.shop_id !== attempt.shop_id)) {
        throw new Error('Shop verification identity mismatch');
      }
      if (mounted.current) setRecoveries((previous) => ({ ...previous, [key]: {
        attempt, phase: 'checked', snapshot, message: snapshot ? '已读取当前记录，请核对后再决定是否提交。' : '未查到记录，不能据此确认原提交未生效。',
      } }));
      setRefresh((value) => value + 1);
    } catch (error: unknown) {
      if (!mounted.current) return;
      if (error instanceof ApiError) blockReads((error.retryAfter ?? 0) * 1000);
      setRecoveries((previous) => ({ ...previous, [key]: { attempt, phase: 'failed', message: '尚未完成核对，请稍后重试。' } }));
    }
  }
  function feedback(key: string) {
    const failure = failures[key]; const recovery = recoveries[key];
    if (!failure && !cooldown[key]) return null;
    return <div className="space-y-2">
      {failure && <Alert status="danger"><Alert.Content><Alert.Title>操作未完成</Alert.Title><Alert.Description>{failure.message}</Alert.Description></Alert.Content></Alert>}
      {cooldown[key] && <p role="status">请求冷却中，请稍后再操作当前店铺。</p>}
      {recovery && <div className="space-y-2">
        <Button variant="secondary" size="sm" isDisabled={recovery.phase === 'checking' || readBlocked} onPress={() => { void verify(key); }}>{recovery.phase === 'checking' ? '核对中…' : '读取当前记录核对'}</Button>
        {recovery.message && <p role="status">{recovery.message}</p>}
        <p className="erp-table-meta">{recovery.attempt.appPair ? '读取结果不会显示密钥；再次保存会重新写入配置。' : recovery.attempt.kind === 'authorize' ? '重新获取会生成新的授权链接。' : '查询当前记录不能证明原请求的提交结果。'}</p>
        {recovery.snapshot && <p className="erp-table-meta">当前记录：{recovery.snapshot.account_name ?? recovery.snapshot.shop_id} · {recovery.snapshot.opened_date ?? '未填开店日期'} · Service {recovery.snapshot.service_id ?? '未配置'}</p>}
      </div>}
    </div>;
  }
  function updateFilter(field: keyof Filters, value: string) { setOffset(0); setFilters((previous) => ({ ...previous, [field]: value })); }
  const dialogKey = shopKey(dialog?.shop);
  const dialogPending = Boolean(dialog && pending[dialogKey]);
  return <div className="erp-page shops-page">
    <PageIntro eyebrow="SHOP CONNECTIONS" title="店铺接入管理" description="从插件发现到 App 配置与授权，在这里完成每家店铺的接入。">
      <Button variant="secondary" className="h-11" isDisabled={readBlocked || list.loading} onPress={() => setRefresh((value) => value + 1)}>刷新店铺</Button>
      <Button className="h-11" isDisabled={blocked('register')} onPress={() => open('register')}>手工补录</Button>
    </PageIntro>
    <form aria-label="店铺筛选" className="erp-filters shops-filters" onSubmit={(event) => { event.preventDefault(); updateFilter('q', search); }}>
      <TextField className="min-w-0" value={search} onChange={setSearch}>
        <Label>店铺搜索</Label><Input fullWidth className="h-11" type="search" placeholder="店铺名称 / ID / 编码" />
      </TextField>
      {filterOptions.map((field) => <Select key={field.key} fullWidth selectedKey={filters[field.key] || 'all'} onSelectionChange={(key) => updateFilter(field.key, key == null || key === 'all' ? '' : String(key))}>
        <Label>{field.label}</Label><Select.Trigger className="h-11"><Select.Value /><Select.Indicator /></Select.Trigger>
        <Select.Popover><ListBox>{field.options.map(([value, label]) => <ListBox.Item key={value} id={value} textValue={label}>{label}<ListBox.ItemIndicator /></ListBox.Item>)}</ListBox></Select.Popover>
      </Select>)}
      <div className="shops-filter-actions"><Button type="submit" className="h-11">搜索</Button><Button type="button" variant="secondary" className="h-11" onPress={() => { setSearch(''); setFilters(initialFilters); setOffset(0); }}>重置</Button></div>
    </form>
    {!canWrite && <p className="erp-table-meta">当前为只读权限，配置和授权需要写入权限。</p>}
    {notice && <p role="status">{notice}</p>}
    {!dialog && Object.keys(failures).map((key) => <div key={key}>{feedback(key)}</div>)}
    <section className="shops-list" aria-labelledby="shops-list-heading" aria-busy={list.loading}>
    <header className="shops-list-header"><div><h3 id="shops-list-heading" className="erp-section-heading">店铺列表</h3><p className="erp-table-meta">已登记 {list.total ?? '—'} 家店铺 · 未授权店铺仅在此页展示</p></div>{list.loading && <p role="status" className="erp-table-meta">正在读取店铺…</p>}</header>
    {list.error && <Alert status="danger"><Alert.Content><Alert.Title>列表读取失败</Alert.Title><Alert.Description>{list.error}</Alert.Description><Button variant="secondary" isDisabled={readBlocked} onPress={() => setRefresh((value) => value + 1)}>重试</Button></Alert.Content></Alert>}
    {list.shops && <Table variant="secondary" className="erp-table shops-table"><Table.ScrollContainer className="erp-table-scroll" tabIndex={0} role="region" aria-label="店铺表格"><Table.Content aria-label="店铺接入列表" className="erp-table-content shops-table-content"><Table.Header className="erp-table-head">
      {['店铺', '区域 / 开店日期', '插件资料同步', 'App 配置', 'OAuth 授权', '操作'].map((name, index) => <Table.Column className={`erp-table-column shops-column-${index}`} key={name} isRowHeader={index === 0}>{name}</Table.Column>)}
    </Table.Header><Table.Body className="erp-table-body" renderEmptyState={() => <div className="erp-table-empty">没有符合条件的店铺。绑定插件后会自动登记，也可以手工补录。</div>}>{list.shops.map((shop) => <Table.Row className="erp-table-row" key={shop.id} id={shop.id}>
      <Table.Cell className="erp-table-cell"><p className="erp-table-primary shops-name">{shop.account_name ?? '名称待同步'}</p><p className="erp-table-id mt-1">{shop.shop_id}</p>{shop.shop_code && <p className="erp-table-meta">编码 {shop.shop_code}</p>}{shop.discovered_at && <p className="erp-table-meta">首次发现 {localTime(shop.discovered_at)}</p>}</Table.Cell>
      <Table.Cell className="erp-table-cell"><p className="font-medium">{shop.region ?? '区域待同步'}</p><p className="erp-table-meta">{shop.opened_date ?? '未填开店日期'}</p></Table.Cell>
      <Table.Cell className="erp-table-cell">{shop.plugin_sources?.length ? shop.plugin_sources.map((source) => <div key={source.source} className="mb-2 last:mb-0"><p>{sourceLabels[source.source] ?? source.source}</p><p className="erp-table-meta">{localTime(source.last_received_at)}</p></div>) : <span className="erp-table-meta">尚无插件上报</span>}</Table.Cell>
      <Table.Cell className="erp-table-cell"><Chip size="sm" variant="soft" color={shop.app_credentials_configured ? 'success' : 'default'}>{shop.app_credentials_configured ? '已配置' : '未配置'}</Chip><p className="erp-table-meta">Service ID</p><p className="erp-table-id">{shop.service_id ?? '未填 Service ID'}</p></Table.Cell>
      <Table.Cell className="erp-table-cell"><Chip size="sm" variant="soft" color={shop.auth_state === 'authorized' ? 'success' : shop.auth_state === 'reauthorize' ? 'warning' : 'default'}>{authLabels[shop.auth_state ?? 'unknown']}</Chip>{shop.auth_state === 'authorized' && <p className="erp-table-meta">自动同步已接入</p>}{shop.auth_state === 'reauthorize' && <p className="erp-table-meta">请重新完成店铺授权</p>}</Table.Cell>
      <Table.Cell className="erp-table-cell" focusMode="child"><div className="shops-row-actions"><Button variant="secondary" size="sm" isDisabled={blocked(shopKey(shop))} onPress={() => open('edit', shop)}>编辑信息</Button><Button variant="secondary" size="sm" isDisabled={blocked(shopKey(shop))} onPress={() => open('app', shop)}>配置 App</Button><Button className="shops-authorize" variant="tertiary" size="sm" isDisabled={blocked(shopKey(shop)) || !shop.app_credentials_configured || !shop.service_id} onPress={() => { void authorize(shop); }}>{pending[shopKey(shop)] ? '处理中…' : '获取授权链接'}</Button></div></Table.Cell>
    </Table.Row>)}</Table.Body></Table.Content></Table.ScrollContainer></Table>}
    <footer className="shops-list-footer">
      <OffsetPagination label="店铺分页" offset={offset} pageSize={pageSize} total={list.total}
        hasNext={list.total == null ? (list.shops?.length ?? 0) >= pageSize : undefined}
        disabled={list.loading || readBlocked} onPage={setOffset} />
    </footer>
    </section>
    <Modal.Backdrop isOpen={dialog !== undefined} isDismissable={!dialogPending} isKeyboardDismissDisabled={dialogPending} onOpenChange={(isOpen) => { if (!isOpen) close(); }}><Modal.Container size="lg" scroll="inside"><Modal.Dialog>
      <Modal.CloseTrigger aria-label="关闭店铺表单" isDisabled={dialogPending} /><Modal.Header><Modal.Heading>{dialog?.kind === 'register' ? '手工补录店铺' : dialog?.kind === 'app' ? '配置当前店铺 App' : '编辑店铺信息'}</Modal.Heading></Modal.Header>
      <Modal.Body className="space-y-4">{feedback(dialogKey)}{dialog && <ShopForm key={dialog.kind + '-' + dialogKey} kind={dialog.kind} shop={dialog.shop} pending={dialogPending} blocked={Boolean(cooldown[dialogKey] || !canWrite || (recoveries[dialogKey] && recoveries[dialogKey]?.phase !== 'checked'))} submitLabel={recoveries[dialogKey]?.phase === 'checked' ? '核对后重新保存' : '保存'} errors={failures[dialogKey]?.fieldErrors ?? {}} onFieldEdit={() => { dirty.current = true; setFailures((previous) => { const current = previous[dialogKey]; return current ? { ...previous, [dialogKey]: { ...current, fieldErrors: {} } } : previous; }); }} onSubmit={(draft) => { void save(draft); }} onCancel={close} />}</Modal.Body>
    </Modal.Dialog></Modal.Container></Modal.Backdrop>
    <Modal.Backdrop isOpen={discardOpen} onOpenChange={setDiscardOpen}><Modal.Container size="sm"><Modal.Dialog>
      <Modal.Header><Modal.Heading>放弃本次尚未保存的修改？</Modal.Heading></Modal.Header>
      <Modal.Body>放弃后，本次店铺表单中的修改不会保存。</Modal.Body>
      <Modal.Footer><Button variant="secondary" onPress={() => setDiscardOpen(false)}>继续编辑</Button><Button variant="danger" isDisabled={dialogPending} onPress={() => { if (dialogPending) return; dirty.current = false; setDiscardOpen(false); setDialog(undefined); }}>放弃修改</Button></Modal.Footer>
    </Modal.Dialog></Modal.Container></Modal.Backdrop>
    <Modal.Backdrop isOpen={link !== undefined} onOpenChange={(isOpen) => { if (!isOpen) setLink(undefined); }}><Modal.Container size="lg"><Modal.Dialog>
      <Modal.CloseTrigger aria-label="关闭授权链接" /><Modal.Header><Modal.Heading>为当前店铺授权</Modal.Heading></Modal.Header>
      <Modal.Body className="space-y-4">{link && <><div><p className="font-semibold">{link.shop.account_name ?? link.shop.shop_id}</p><p className="erp-table-meta break-all">店铺 {link.shop.shop_id} · Service {link.shop.service_id}</p></div><p>请使用此店铺完成授权。成功后系统会自动开始同步。</p><p className="erp-table-meta">链接有效至 {localTime(link.value.state_expires_at)}，仅可使用一次。</p><p className="rounded-lg border border-[var(--rule)] p-3 text-sm break-all">{link.value.authorize_url}</p><div className="flex flex-wrap gap-3"><a className={buttonVariants({ variant: 'primary' })} href={link.value.authorize_url} target="_blank" rel="noopener noreferrer">打开授权页面</a><Button variant="secondary" onPress={() => { void navigator.clipboard.writeText(link.value.authorize_url).then(() => setNotice('授权链接已复制'), () => setNotice('复制失败，请手动选择链接复制')); }}>复制链接</Button></div>{notice && <p role="status">{notice}</p>}</>}</Modal.Body>
    </Modal.Dialog></Modal.Container></Modal.Backdrop>
  </div>;
}
