import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Modal, Table } from '@heroui/react';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { commandFailure, type CommandFailure } from '../../shared/api/command-errors';
import type { PageProps } from '../../shared/contracts';
import { decodeAuthorization, decodeShopCommand, decodeShopList, decodeTotal, type AuthorizationLink, type Shop } from './contracts';
import ShopForm, { fieldLabels, type ShopDraft, type ShopFormKind } from './ShopForm';

export default function Shops({ bootstrap }: PageProps) {
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const [refresh, setRefresh] = useState(0);
  const [list, setList] = useState<{ loading: boolean; shops?: Shop[]; total?: number | null; error?: string; blocked?: boolean }>({ loading: true });
  const [dialog, setDialog] = useState<{ kind: ShopFormKind; shop?: Shop }>();
  const [pending, setPending] = useState(false);
  const [cooldown, setCooldown] = useState(false);
  const [failure, setFailure] = useState<CommandFailure>();
  const [notice, setNotice] = useState('');
  const [link, setLink] = useState<AuthorizationLink>();
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const commandDelay = useRef<(() => void) | undefined>(undefined);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; commandDelay.current?.(); }; }, []);
  useEffect(() => {
    const controller = new AbortController(); let current = true; let stopDelay: (() => void) | undefined;
    setList({ loading: true });
    requestJson<unknown>(bootstrap.rootPath, '/v2/commerce/channel-accounts?platform=tiktok&limit=500', { signal: controller.signal })
      .then(({ data, headers }) => { const shops = decodeShopList(data); if (current) setList({ loading: false, shops, total: decodeTotal(headers.get('X-Total-Count')) }); })
      .catch((error: unknown) => {
        if (!current) return;
        const delay = error instanceof ApiError ? (error.retryAfter ?? 0) * 1000 : 0;
        setList({ loading: false, error: error instanceof ApiError ? `加载店铺失败（HTTP ${error.status}）` : '店铺列表不可用，请刷新重试', blocked: delay > 0 });
        if (delay > 0) stopDelay = scheduleReadDelay(() => { if (current) setList((previous) => ({ ...previous, blocked: false })); }, delay);
      });
    return () => { current = false; controller.abort(); stopDelay?.(); };
  }, [bootstrap.rootPath, refresh]);
  function beginCommand() {
    if (!canWrite || inFlight.current || cooldown) return false;
    inFlight.current = true; setPending(true); setFailure(undefined); setNotice(''); return true;
  }
  function endCommand() { inFlight.current = false; if (mounted.current) setPending(false); }
  function rejectCommand(error: unknown) {
    if (!mounted.current) return;
    const result = commandFailure(error, fieldLabels); setFailure(result);
    if (result.retryAfter > 0) {
      commandDelay.current?.(); setCooldown(true);
      commandDelay.current = scheduleReadDelay(() => { if (mounted.current) setCooldown(false); }, result.retryAfter * 1000);
    }
  }
  function open(kind: ShopFormKind, shop?: Shop) {
    if (!canWrite || inFlight.current || cooldown) return;
    setFailure(undefined); setNotice(''); setDialog({ kind, shop });
  }
  function close() { if (!inFlight.current) { setDialog(undefined); setFailure(undefined); } }
  async function save(draft: ShopDraft) {
    if (!dialog || !canWrite || inFlight.current || cooldown) return;
    const appKey = draft.app_key.trim(); const secret = draft.app_secret;
    if ((Boolean(appKey) !== Boolean(secret)) || (appKey && !draft.service_id.trim())) {
      const fieldErrors: Record<string, string> = !draft.service_id.trim() && appKey ? { service_id: '配置 App 凭证时必须填写 Service ID' }
        : { app_key: 'App Key 与 App Secret 必须成对填写', app_secret: 'App Key 与 App Secret 必须成对填写' };
      setFailure({ message: '请检查 App 凭证输入', fieldErrors, uncertain: false, retryAfter: 0 }); return;
    }
    if (!beginCommand()) return;
    const target = dialog;
    const body: Record<string, string> = {};
    if (target.kind === 'register') { body.platform = 'tiktok'; body.shop_id = draft.shop_id.trim(); }
    const metadata = target.kind === 'app' ? ['service_id'] as const : ['account_name', 'region', 'opened_date', 'service_id'] as const;
    for (const key of metadata) if (draft[key].trim()) body[key] = draft[key].trim();
    if (target.kind !== 'edit' && appKey && secret) { body.app_key = appKey; body.app_secret = secret; }
    try {
      const register = target.kind === 'register';
      const id = target.shop?.id;
      if (!register && id === undefined) throw new Error('未选定店铺');
      const { data } = await requestJson<unknown>(bootstrap.rootPath, register ? '/v2/admin/shops/register' : `/v2/admin/shops/${id}`, { method: register ? 'POST' : 'PATCH', body: JSON.stringify(body) });
      decodeShopCommand(data, register ? { shop_id: body.shop_id, register: true } : { id });
      if (mounted.current) { setDialog(undefined); setNotice('店铺保存已确认'); setLink(undefined); setRefresh((value) => value + 1); }
    } catch (error: unknown) { rejectCommand(error); } finally { endCommand(); }
  }
  async function copyLink(value: string, automatic = false) {
    try { await navigator.clipboard.writeText(value); if (mounted.current) setNotice('授权链接已复制；state 单次使用'); }
    catch { if (mounted.current) setNotice(automatic ? '授权链接已生成；自动复制失败，请使用复制链接或手动选择链接' : '复制失败，请手动选择链接'); }
  }
  async function authorize(shop: Shop) {
    if (!beginCommand()) return;
    setLink(undefined);
    try {
      const params = new URLSearchParams({ format: 'json' }); if (shop.service_id) params.set('service_id', shop.service_id);
      // A GET command that creates one-shot state: explicit action only, never read polling/retry.
      const { data } = await requestJson<unknown>(bootstrap.rootPath, `/v2/oauth/tiktok/authorize?${params}`);
      const result = decodeAuthorization(data);
      if (mounted.current) { setLink(result); await copyLink(result.authorize_url, true); }
    } catch (error: unknown) { rejectCommand(error); } finally { endCommand(); }
  }
  const disabled = !canWrite || pending || cooldown;
  const commandAlert = failure && <Alert status="danger"><Alert.Content><Alert.Title>提交未完成</Alert.Title><Alert.Description>{failure.message}</Alert.Description></Alert.Content></Alert>;
  return <div className="space-y-4">
    <div className="flex flex-wrap gap-3"><Button isDisabled={disabled} onPress={() => open('register')}>注册店铺</Button><Button variant="secondary" isDisabled={list.blocked} onPress={() => setRefresh((value) => value + 1)}>刷新店铺</Button></div>
    {!canWrite && <p>当前权限仅浏览；注册、编辑、App 配置和授权链接需要 readwrite 权限，以服务端判定为准。</p>}
    <p aria-live="polite">已注册总数：{list.total == null ? '未知' : list.total} · 本次最多读取 500 条</p>
    <p>App 凭据配置不等于 OAuth 授权；同步方式沿用凭据关联展示，有效性以服务端为准。</p>
    {cooldown && <p role="status">请等待服务端指定时间后再提交；不会自动重试，仍可取消或修改草稿。</p>}
    {notice && <p role="status">{notice}</p>}
    {!dialog && commandAlert}
    {list.loading && <p role="status">加载店铺中…</p>}
    {list.error && <Alert status="danger"><Alert.Content><Alert.Title>列表不可用</Alert.Title><Alert.Description>{list.error}</Alert.Description><Button isDisabled={list.blocked} onPress={() => setRefresh((value) => value + 1)}>重试店铺</Button></Alert.Content></Alert>}
    {link && <section aria-label="授权链接" className="space-y-2 break-all"><p>state 单次使用；有效至 {new Date(link.state_expires_at).toLocaleString('zh-CN', { timeZone: 'UTC' })} UTC</p><a href={link.authorize_url} target="_blank" rel="noopener noreferrer">打开授权页</a><p>{link.authorize_url}</p><Button variant="secondary" onPress={() => { void copyLink(link.authorize_url); }}>复制链接</Button></section>}
    {list.shops && <Table><Table.ScrollContainer><Table.Content aria-label="已注册店铺"><Table.Header>
      {['店铺 ID', '店铺名称', '区域', 'Service ID', 'App 凭据', '开店日期', '同步方式 / OAuth 凭据', '状态', '操作'].map((name, index) => <Table.Column key={name} isRowHeader={index === 0}>{name}</Table.Column>)}
    </Table.Header><Table.Body renderEmptyState={() => '暂无店铺'}>{list.shops.map((shop) => <Table.Row key={shop.id} id={shop.id}>
      <Table.Cell><span className="block max-w-64 break-all">{shop.shop_id}</span></Table.Cell><Table.Cell><span className="block max-w-64 break-all">{shop.account_name ?? '—'}</span></Table.Cell><Table.Cell>{shop.region ?? '—'}</Table.Cell><Table.Cell><span className="block max-w-48 break-all">{shop.service_id ?? '—'}</span></Table.Cell>
      <Table.Cell>{shop.app_credentials_configured ? '已配置' : '未配置'}</Table.Cell><Table.Cell>{shop.opened_date ?? '—'}</Table.Cell><Table.Cell><p>{shop.credential_id === null ? '仅插件' : 'API 同步'}</p><p>{shop.credential_id === null ? '未关联' : `已关联 #${shop.credential_id}`}</p></Table.Cell><Table.Cell>{shop.status ?? '—'}</Table.Cell>
      <Table.Cell focusMode="child"><div className="flex flex-wrap gap-2"><Button size="sm" isDisabled={disabled} onPress={() => open('edit', shop)}>编辑元信息</Button><Button size="sm" isDisabled={disabled} onPress={() => open('app', shop)}>配置 App</Button><Button size="sm" isDisabled={disabled} onPress={() => { void authorize(shop); }}>获取授权链接</Button></div></Table.Cell>
    </Table.Row>)}</Table.Body></Table.Content></Table.ScrollContainer></Table>}
    <Modal.Backdrop isOpen={dialog !== undefined} isDismissable={!pending} isKeyboardDismissDisabled={pending} onOpenChange={(isOpen) => { if (!isOpen) close(); }}><Modal.Container size="lg" scroll="inside"><Modal.Dialog>
      <Modal.CloseTrigger aria-label="关闭店铺表单" isDisabled={pending} /><Modal.Header><Modal.Heading>{dialog?.kind === 'register' ? '注册店铺' : dialog?.kind === 'app' ? '配置 App' : '编辑元信息'}</Modal.Heading></Modal.Header>
      <Modal.Body className="space-y-3">{commandAlert}{dialog && <ShopForm key={`${dialog.kind}-${dialog.shop?.id ?? 'new'}`} kind={dialog.kind} shop={dialog.shop} pending={pending} blocked={cooldown || !canWrite} errors={failure?.fieldErrors ?? {}} onSubmit={(draft) => { void save(draft); }} onCancel={close} />}</Modal.Body>
    </Modal.Dialog></Modal.Container></Modal.Backdrop>
  </div>;
}
