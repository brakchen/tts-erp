import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Label, ListBox, Modal, Select, Table } from '@heroui/react';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { commandFailure, type CommandFailure } from '../../shared/api/command-errors';
import type { PageProps } from '../../shared/contracts';
import { decodeEnumDelete, decodeEnumList, decodeEnumUpsert, parseSortOrder, type EnumRow } from './contracts';
import EnumForm, { fieldLabels, type EnumDraft } from './EnumForm';

const knownCategories: Record<string, string> = { order_status: '订单状态', case_type: '售后类型', case_status: '售后状态', settle_component: '结算组件', cost_source: '成本来源', shipment_status: '物流状态', column_header: '表头翻译' };
const categoryName = (type: string) => Object.hasOwn(knownCategories, type) ? `${knownCategories[type]}（${type}）` : type || '（空分类）';
const listPath = '/v2/config/enum-map/list';
type Dialog = { kind: 'add' | 'edit' | 'delete'; enum_type: string; row?: EnumRow };
interface Attempt { kind: Dialog['kind']; enum_type: string; enum_value: string; id?: number }
interface Recovery { attempt: Attempt; phase: 'unverified' | 'checking' | 'checked' | 'failed'; message?: string; snapshot?: EnumRow }

export default function EnumMap({ bootstrap }: PageProps) {
  const canManage = bootstrap.roleCapabilities?.admin === true;
  const [list, setList] = useState<{ loading: boolean; rows?: EnumRow[]; error?: string }>({ loading: true });
  const [category, setCategory] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [dialog, setDialog] = useState<Dialog>();
  const [pending, setPending] = useState(false);
  const [cooldown, setCooldown] = useState(false);
  const [readBlocked, setReadBlocked] = useState(false);
  const [failure, setFailure] = useState<CommandFailure>();
  const [recovery, setRecovery] = useState<Recovery>();
  const [notice, setNotice] = useState('');
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const recoveryGate = useRef<Recovery['phase'] | 'none'>('none');
  const readNotBefore = useRef(0);
  const readDelay = useRef<(() => void) | undefined>(undefined);
  const commandDelay = useRef<(() => void) | undefined>(undefined);
  const verification = useRef<AbortController | undefined>(undefined);
  const needsCheck = recovery !== undefined && recovery.phase !== 'checked';
  // Display-only categories/filtering; neither sorting nor business aggregation is performed here.
  const categories = [...new Set([...(list.rows ?? []).map((row) => row.enum_type), ...Object.keys(knownCategories), ...(category === null ? [] : [category])])];
  const activeType = category ?? categories[0];
  const blockReads = useCallback((milliseconds: number) => {
    if (milliseconds <= 0) return;
    readNotBefore.current = Math.max(readNotBefore.current, performance.now() + milliseconds);
    readDelay.current?.(); setReadBlocked(true);
    readDelay.current = scheduleReadDelay(() => { if (mounted.current) setReadBlocked(false); }, readNotBefore.current - performance.now());
  }, []);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; readDelay.current?.(); commandDelay.current?.(); verification.current?.abort(); }; }, []);
  useEffect(() => {
    if (performance.now() < readNotBefore.current) return;
    const controller = new AbortController(); let current = true;
    setList({ loading: true });
    requestJson<unknown>(bootstrap.rootPath, listPath, { signal: controller.signal })
      .then(({ data }) => { const rows = decodeEnumList(data); if (current) { setList({ loading: false, rows }); setCategory((previous) => previous ?? rows[0]?.enum_type ?? 'order_status'); } })
      .catch((error: unknown) => {
        if (!current) return;
        if (error instanceof ApiError) blockReads((error.retryAfter ?? 0) * 1000);
        setList({ loading: false, error: error instanceof ApiError ? `加载映射失败（HTTP ${error.status}）` : '枚举列表不可用，请明确刷新重试' });
      });
    return () => { current = false; controller.abort(); };
  }, [bootstrap.rootPath, refresh, blockReads]);
  const mayStart = () => canManage && !inFlight.current && !cooldown && (recoveryGate.current === 'none' || recoveryGate.current === 'checked');
  function open(target: Dialog) {
    if (!mayStart()) return;
    recoveryGate.current = 'none'; setRecovery(undefined); setFailure(undefined); setNotice(''); setDialog(target);
  }
  function close() { if (!inFlight.current) { setDialog(undefined); if (!failure?.uncertain) setFailure(undefined); } }
  function reject(error: unknown, attempt: Attempt) {
    if (!mounted.current) return;
    const result = commandFailure(error, fieldLabels); setFailure(result);
    if (result.uncertain) { recoveryGate.current = 'unverified'; setRecovery({ attempt, phase: 'unverified' }); }
    if (result.retryAfter > 0) {
      commandDelay.current?.(); setCooldown(true);
      commandDelay.current = scheduleReadDelay(() => { if (mounted.current) setCooldown(false); }, result.retryAfter * 1000);
    }
  }
  async function submit(draft?: EnumDraft) {
    if (!dialog || !mayStart()) return;
    const target = dialog;
    const enum_value = target.row?.enum_value ?? draft?.enum_value.trim() ?? '';
    const attempt: Attempt = { kind: target.kind, enum_type: target.enum_type, enum_value, id: target.row?.id };
    let body: { enum_type: string; enum_value: string; label_zh: string; sort_order: number } | undefined;
    if (target.kind !== 'delete') {
      if (!draft) return;
      const errors: Record<string, string> = {};
      for (const [key, text, limit] of [['enum_type', target.enum_type, 64], ['enum_value', enum_value, 128], ['label_zh', draft.label_zh.trim(), 256]] as const) {
        if (!text || Array.from(text).length > limit) errors[key] = `${fieldLabels[key]}：请填写 1–${limit} 个字符`;
      }
      let sort_order = 0;
      try { sort_order = parseSortOrder(draft.sort_order); } catch { errors.sort_order = '排序必须是非负整数且在安全整数范围内'; }
      if (Object.keys(errors).length) { setFailure({ message: '请检查输入', fieldErrors: errors, uncertain: false, retryAfter: 0 }); return; }
      body = { enum_type: target.enum_type, enum_value, label_zh: draft.label_zh.trim(), sort_order };
    }
    inFlight.current = true; setPending(true); recoveryGate.current = 'none'; setRecovery(undefined); setFailure(undefined); setNotice('');
    try {
      if (target.kind === 'delete') {
        if (target.row === undefined) throw new Error('未选定映射');
        const { data } = await requestJson<unknown>(bootstrap.rootPath, `/v2/config/enum-map/${target.row.id}`, { method: 'DELETE' });
        decodeEnumDelete(data, target.row.id);
      } else {
        const { data } = await requestJson<unknown>(bootstrap.rootPath, '/v2/config/enum-map', { method: 'PUT', body: JSON.stringify(body) });
        decodeEnumUpsert(data);
      }
      if (mounted.current) { setDialog(undefined); setNotice(target.kind === 'delete' ? '映射删除已确认' : '映射保存已确认'); setRefresh((value) => value + 1); }
    } catch (error: unknown) { reject(error, attempt); }
    finally { inFlight.current = false; if (mounted.current) setPending(false); }
  }
  async function verifyAttempt() {
    if (!recovery || recoveryGate.current === 'checking' || inFlight.current || performance.now() < readNotBefore.current) return;
    const attempt = recovery.attempt; const controller = new AbortController(); verification.current = controller;
    recoveryGate.current = 'checking'; setRecovery({ attempt, phase: 'checking' });
    try {
      const { data } = await requestJson<unknown>(bootstrap.rootPath, listPath, { signal: controller.signal });
      const rows = decodeEnumList(data);
      const snapshot = rows.find((row) => attempt.kind === 'delete' ? row.id === attempt.id : row.enum_type === attempt.enum_type && row.enum_value === attempt.enum_value);
      if (snapshot && (snapshot.enum_type !== attempt.enum_type || snapshot.enum_value !== attempt.enum_value)) throw new Error('核对身份不匹配');
      if (mounted.current) { recoveryGate.current = 'checked'; setRecovery({ attempt, phase: 'checked', snapshot, message: snapshot ? '公开列表已读取，当前记录如下；不证明原提交结果。' : '公开列表已读取，当前未找到此记录；不证明原提交未生效或已回滚。' }); }
    } catch (error: unknown) {
      if (!mounted.current || controller.signal.aborted) return;
      if (error instanceof ApiError) blockReads((error.retryAfter ?? 0) * 1000);
      recoveryGate.current = 'failed'; setRecovery({ attempt, phase: 'failed', message: '核对未完成，草稿仍保留；请明确重试查询。' });
    } finally { if (verification.current === controller) verification.current = undefined; }
  }
  const disabled = !canManage || pending || cooldown || needsCheck;
  const alert = failure && <Alert status="danger"><Alert.Content><Alert.Title>提交未完成</Alert.Title><Alert.Description>{failure.message}</Alert.Description></Alert.Content></Alert>;
  const check = failure?.uncertain && recovery && <section aria-label="提交结果核对" className="space-y-2">
    <Button variant="secondary" isDisabled={recovery.phase === 'checking' || readBlocked} onPress={() => { void verifyAttempt(); }}>查询核对</Button>
    {recovery.phase === 'checking' && <p role="status">核对中…</p>}{recovery.message && <p role="status">{recovery.message}</p>}
    {readBlocked && <p>读取冷却中，请等待服务端指定时间。</p>}
    <p className="break-all">本次核对：{recovery.attempt.enum_type} / {recovery.attempt.enum_value}</p>
    <p>不会自动重放原命令；核对后是否重新提交由你明确决定。</p>
    {recovery.snapshot && <p className="break-all">当前公开值：{recovery.snapshot.enum_type} / {recovery.snapshot.enum_value} · {recovery.snapshot.label_zh} · 排序 {recovery.snapshot.sort_order}</p>}
  </section>;
  return <div className="space-y-4">
    <div className="flex flex-wrap gap-3"><Button isDisabled={disabled} onPress={() => open({ kind: 'add', enum_type: activeType })}>新增映射</Button><Button variant="secondary" isDisabled={readBlocked} onPress={() => setRefresh((value) => value + 1)}>刷新映射</Button></div>
    {!canManage && <p>当前仅浏览；维护需要 admin 权限，以服务端判定为准。</p>}
    {pending && <p role="status">提交中，请等待确认…</p>}{cooldown && <p role="status">请等待服务端指定时间后再提交；仍可修改或取消草稿，不会自动重试。</p>}
    {notice && <p role="status">{notice}</p>}{!dialog && <>{alert}{check}</>}
    {list.loading && <p role="status">加载映射中…</p>}{list.error && <Alert status="danger"><Alert.Content><Alert.Title>列表不可用</Alert.Title><Alert.Description>{list.error}</Alert.Description><Button isDisabled={readBlocked} onPress={() => setRefresh((value) => value + 1)}>重试映射</Button></Alert.Content></Alert>}
    {/* Public v3 Select API, not v2 selectedKeys: https://heroui.com/en/docs/react/components/select */}
    <Select value={activeType} onChange={(key) => { if (typeof key === 'string') setCategory(key); }} className="max-w-full">
      <Label>分类</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox>{categories.map((type) => <ListBox.Item key={type} id={type} textValue={categoryName(type)}>{categoryName(type)}</ListBox.Item>)}</ListBox></Select.Popover>
    </Select>
    {list.rows && <><p>显示顺序沿用服务端；字段较多时可在表格区域横向滚动。</p><Table><Table.ScrollContainer role="region" aria-label="枚举表格"><Table.Content aria-label="枚举映射" className="min-w-[44rem]"><Table.Header>
      <Table.Column isRowHeader>英文值</Table.Column><Table.Column>中文标签</Table.Column><Table.Column>排序</Table.Column>{canManage && <Table.Column>操作</Table.Column>}
    </Table.Header><Table.Body renderEmptyState={() => '该分类暂无映射'}>{list.rows.filter((row) => row.enum_type === activeType).map((row) => <Table.Row key={row.id} id={row.id}>
      <Table.Cell><span className="block w-48 break-all">{row.enum_value}</span></Table.Cell><Table.Cell><span className="block w-56 break-all">{row.label_zh}</span></Table.Cell><Table.Cell>{row.sort_order}</Table.Cell>
      {canManage && <Table.Cell focusMode="child"><div className="flex flex-wrap gap-2"><Button size="sm" isDisabled={disabled} onPress={() => open({ kind: 'edit', enum_type: row.enum_type, row })}>编辑映射</Button><Button size="sm" variant="danger" isDisabled={disabled} onPress={() => open({ kind: 'delete', enum_type: row.enum_type, row })}>删除映射</Button></div></Table.Cell>}
    </Table.Row>)}</Table.Body></Table.Content></Table.ScrollContainer></Table></>}
    <Modal.Backdrop isOpen={dialog !== undefined} isDismissable={!pending} isKeyboardDismissDisabled={pending} onOpenChange={(isOpen) => { if (!isOpen) close(); }}><Modal.Container size="lg" scroll="inside"><Modal.Dialog>
      <Modal.CloseTrigger aria-label="关闭枚举表单" isDisabled={pending} /><Modal.Header><Modal.Heading>{dialog?.kind === 'delete' ? '确认删除映射' : dialog?.kind === 'edit' ? '编辑映射' : '新增映射'}</Modal.Heading></Modal.Header>
      <Modal.Body className="space-y-3">{alert}{check}{dialog && <><p className="break-all">分类：{categoryName(dialog.enum_type)}</p>{failure?.fieldErrors.enum_type && <p>{failure.fieldErrors.enum_type}</p>}
        {dialog.kind === 'delete' ? <><p className="break-all">确认删除 {dialog.enum_type} / {dialog.row?.enum_value}？</p><p>此操作删除配置映射，仍须通过服务端安全检查；不会自动重试。</p><div className="flex flex-wrap gap-3"><Button variant="danger" isDisabled={disabled} onPress={() => { void submit(); }}>{recovery?.phase === 'checked' ? '核对后明确再次删除' : '确认删除'}</Button><Button variant="secondary" isDisabled={pending} onPress={close}>取消</Button></div></>
          : <EnumForm key={`${dialog.kind}-${dialog.row?.id ?? 'new'}`} row={dialog.row} pending={pending} blocked={disabled} errors={failure?.fieldErrors ?? {}} submitLabel={recovery?.phase === 'checked' ? '核对后明确重新提交' : '保存映射'} onSubmit={(draft) => { void submit(draft); }} onCancel={close} />}</>}</Modal.Body>
    </Modal.Dialog></Modal.Container></Modal.Backdrop>
  </div>;
}
