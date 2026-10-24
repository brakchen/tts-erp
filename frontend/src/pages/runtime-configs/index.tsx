import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Label, Modal, Table, TextField } from '@heroui/react';
import { ApiError } from '../../shared/api/client';
import type { PageProps } from '../../shared/contracts';
import { decodeDetail, decodeItem, decodeList, decodeRevision, decodeSecret, decodeSnapshot, record, type Detail, type Item, type Published, type Revision, type Secret } from './contracts';
import { parseObject, parseRuntime, stringifyRuntime } from './json';
import JsonEditor from './JsonEditor';
import CreateConfig, { type CreateDraft } from './CreateConfig';
import SecretPanel from './SecretPanel';
import { useRuntimeCommands, type Attempt } from './useRuntimeCommands';

interface Confirmation { attempt: Attempt; path: string; body?: string; title: string; message: string; success: string }
interface ReadState<T> { data?: T; loading: boolean; error?: string }
const errorMessage = (error: unknown) => error instanceof ApiError ? `读取失败（HTTP ${error.status}），请明确重试` : '读取失败，请明确重试';

export default function RuntimeConfigs({ bootstrap }: PageProps) {
  const writable = bootstrap.roleCapabilities?.readwrite === true;
  const commands = useRuntimeCommands(bootstrap.rootPath, writable);
  const [items, setItems] = useState<ReadState<Item[]>>({ loading: true });
  const [secrets, setSecrets] = useState<ReadState<Secret[]>>({ loading: writable });
  const [detail, setDetail] = useState<Detail>();
  const [published, setPublished] = useState<{ key: string; name: string; data?: Published }>();
  const [history, setHistory] = useState<ReadState<Revision[]>>({ loading: false });
  const [selectedError, setSelectedError] = useState(''); const [selecting, setSelecting] = useState(false);
  const [payload, setPayload] = useState(''); const [savedPayload, setSavedPayload] = useState(''); const [comment, setComment] = useState('');
  const [includeRetired, setIncludeRetired] = useState(false);
  const [listsLoading, setListsLoading] = useState(true);
  const [confirmation, setConfirmation] = useState<Confirmation>();
  const [snapshotNotice, setSnapshotNotice] = useState('');
  const selectedRequest = useRef(0);
  const selectedKey = useRef<string | undefined>(undefined);
  const secretRequest = useRef(0);
  const snapshot = useRef<{ etag?: string; items: Record<string, Published> } | undefined>(undefined);
  const alive = useRef(true);
  const listsFlight = useRef(false); const selectFlight = useRef(false); const previewFlight = useRef(false);
  const [previewing, setPreviewing] = useState(false);
  const disabled = commands.disabled || previewing || selecting;
  const dirty = payload !== savedPayload;

  useEffect(() => { alive.current = true; return () => { alive.current = false; selectedRequest.current++; }; }, []);
  async function loadLists(retired = includeRetired) {
    if (!commands.canRead() || listsFlight.current) return;
    listsFlight.current = true; setListsLoading(true);
    setItems((old) => ({ ...old, loading: true, error: undefined }));
    const suffix = writable && retired ? '?includeRetired=true' : '';
    const list = commands.read<unknown>(`/items${suffix}`, {}, parseRuntime).then(({ data }) => {
      const rows = decodeList(data, decodeItem); if (alive.current) setItems({ data: rows, loading: false });
    }).catch((error: unknown) => { if (alive.current) setItems((old) => ({ ...old, loading: false, error: errorMessage(error) })); });
    const secretList = writable ? loadSecrets(suffix) : Promise.resolve();
    try { await Promise.all([list, secretList]); }
    finally { listsFlight.current = false; if (alive.current) setListsLoading(false); }
  }
  async function loadSecrets(suffix = includeRetired ? '?includeRetired=true' : '') {
    const request = ++secretRequest.current;
    setSecrets((old) => ({ ...old, loading: true, error: undefined }));
    try { const { data } = await commands.read<unknown>(`/secrets${suffix}`, {}, parseRuntime); const rows = decodeList(data, decodeSecret); if (alive.current && request === secretRequest.current) setSecrets({ data: rows, loading: false }); }
    catch (error: unknown) { if (alive.current && request === secretRequest.current) setSecrets((old) => ({ ...old, loading: false, error: errorMessage(error) })); }
  }
  useEffect(() => { void loadLists(false); /* bootstrap lifetime is owned by the shared shell */ }, [bootstrap.rootPath, writable]);
  async function readSnapshot() {
    const cached = snapshot.current;
    try {
      const { data, headers } = await commands.read<unknown>('/snapshot', { headers: cached?.etag ? { 'If-None-Match': cached.etag } : {} }, parseRuntime);
      const next = { items: decodeSnapshot(data), etag: headers.get('ETag') ?? undefined }; snapshot.current = next; return next.items;
    } catch (error) {
      if (error instanceof ApiError && error.status === 304 && cached?.etag) { setSnapshotNotice('已发布快照未变化（304）'); return cached.items; }
      throw error;
    }
  }
  function applyDetail(row: Detail, preserveComment = false) {
    selectedKey.current = row.configKey;
    setDetail(row); const text = stringifyRuntime(row.draftPayload ?? row.publishedPayload ?? {}, 2);
    setPayload(text); setSavedPayload(text); setPublished(undefined); if (!preserveComment) setComment('');
  }
  async function loadHistory(key: string, request: number) {
    const current = () => alive.current && request === selectedRequest.current && key === selectedKey.current;
    if (!current()) return;
    setHistory({ loading: true });
    try { const { data } = await commands.read<unknown>(`/items/${encodeURIComponent(key)}/revisions`, {}, parseRuntime); const rows = decodeList(data, decodeRevision); if (current()) setHistory({ data: rows, loading: false }); }
    catch (error: unknown) { if (current()) setHistory({ loading: false, error: errorMessage(error) }); }
  }
  async function openItem(row: Item, overwrite = false) {
    if (!commands.canRead() || selectFlight.current || commands.pending) return;
    if ((dirty || comment.length > 0) && detail && !overwrite) { commands.invalid('当前草稿或发布说明尚未提交。请先保存草稿并发布，或明确重新载入所选配置以放弃本地编辑。'); return; }
    const request = ++selectedRequest.current; selectFlight.current = true; setSelecting(true); setSelectedError(''); setSnapshotNotice('');
    try {
      if (writable) {
        const { data } = await commands.read<unknown>(`/items/${encodeURIComponent(row.configKey)}`, {}, parseRuntime); const result = decodeDetail(data, row.configKey);
        if (alive.current && request === selectedRequest.current) { applyDetail(result); await loadHistory(row.configKey, request); }
      } else {
        const values = await readSnapshot(); const value = Object.hasOwn(values, row.configKey) ? values[row.configKey] : undefined;
        if (alive.current && request === selectedRequest.current) { setPublished({ key: row.configKey, name: row.displayName, data: value }); setDetail(undefined); }
      }
    } catch (error: unknown) { if (alive.current && request === selectedRequest.current) setSelectedError(errorMessage(error)); }
    finally { selectFlight.current = false; if (alive.current) setSelecting(false); }
  }
  async function create(draft: CreateDraft) {
    const result = await commands.run({ kind: 'create', key: draft.configKey }, '/items', 'POST', stringifyRuntime(draft), '配置创建已确认');
    if (result && result !== true) { applyDetail(result); void loadLists(); void loadHistory(result.configKey, ++selectedRequest.current); return result; }
  }
  async function saveDraft() {
    if (!detail || disabled || detail.retiredAt) return;
    let parsed: Record<string, unknown>; try { parsed = parseObject(payload); } catch { commands.invalid('草稿配置必须是有效 JSON 对象'); return; }
    const result = await commands.run({ kind: 'draft', key: detail.configKey }, `/items/${encodeURIComponent(detail.configKey)}/draft`, 'PUT', stringifyRuntime({ expectedDraftVersion: detail.draftVersion, payload: parsed }), '草稿保存已确认');
    if (result && result !== true) { applyDetail(result, true); void loadLists(); }
  }
  function confirmItem(kind: 'publish' | 'rollback' | 'retire' | 'restore', target?: number) {
    if (!detail || disabled || (detail.retiredAt && kind !== 'restore')) return;
    const body = kind === 'publish' || kind === 'rollback' ? stringifyRuntime({ expectedDraftVersion: detail.draftVersion, ...(kind === 'rollback' ? { targetVersion: target } : {}), comment: comment || null }) : undefined;
    setConfirmation({ attempt: { kind, key: detail.configKey, target }, path: `/items/${encodeURIComponent(detail.configKey)}/${kind}`, body,
      title: kind === 'publish' ? '确认发布新版本' : kind === 'rollback' ? `确认恢复 v${target}` : kind === 'retire' ? '确认归档配置' : '确认恢复配置',
      message: kind === 'publish' ? '只发布服务器已保存草稿，生成不可变新版本。' : kind === 'rollback' ? '将历史内容复制并发布为更高新版本；不会覆盖历史记录。' : kind === 'retire' ? '归档后配置将不再出现在运行快照；历史保留。' : '恢复后原已发布版本将重新生效；服务器检查凭证引用。',
      success: kind === 'publish' ? '发布已确认' : kind === 'rollback' ? '历史恢复已确认' : kind === 'retire' ? '配置归档已确认' : '配置恢复已确认' });
  }
  async function executeConfirmation() {
    const current = confirmation; if (!current || disabled) return;
    const result = await commands.run(current.attempt, current.path, 'POST', current.body, current.success);
    // Recovery must remain reachable outside the modal focus trap after a failed/unknown command.
    setConfirmation(undefined);
    if (result) { void loadLists(); if (detail && !current.attempt.kind.startsWith('secret-')) void openItem(detail, true); }
  }
  async function preview(data: Record<string, unknown>) {
    if (disabled || previewFlight.current) return;
    previewFlight.current = true; setPreviewing(true);
    try {
      const { data: result } = await commands.read<unknown>('/schema/preview', { method: 'POST', body: stringifyRuntime({ payload: data }) }, parseRuntime);
      if (!record(result) || !record(result.jsonSchema)) throw new Error('Schema 响应无效'); return result.jsonSchema;
    } catch (error) { commands.invalid(errorMessage(error)); }
    finally { previewFlight.current = false; setPreviewing(false); }
  }
  const errors = commands.failure?.fieldErrors ?? {};
  return <div className="min-w-0 space-y-6">
    <p>先保存草稿，再发布不可变版本。财务和业务规则由服务器校验；凭证仅以引用出现。</p>
    {!writable && <p>当前只读；只能查看已发布快照，不能访问草稿、Schema 或凭证管理。</p>}
    <div className="flex flex-wrap items-center gap-3"><Button variant="secondary" isDisabled={commands.blocked || listsLoading} onPress={() => { void loadLists(); }}>刷新配置列表</Button>
      {writable && <label><input type="checkbox" checked={includeRetired} disabled={commands.blocked || listsLoading} onChange={(e) => { if (!commands.canRead() || listsFlight.current) return; setIncludeRetired(e.target.checked); void loadLists(e.target.checked); }} /> 包含已归档配置与凭证</label>}</div>
    {commands.pending && <p role="status">提交中，请等待确认，不要重复操作…</p>}
    {commands.blocked && <p role="status">服务端冷却中；读写都须等待完整 Retry-After，不会自动重试。</p>}
    {commands.notice && <p role="status">{commands.notice}</p>}
    {commands.failure && <Alert status="danger"><Alert.Content><Alert.Title>操作未完成</Alert.Title><Alert.Description>{commands.failure.message}</Alert.Description></Alert.Content></Alert>}
    {commands.recovery && <section aria-label="提交结果核对" className="space-y-3"><p className="break-all">待核对：{commands.recovery.attempt.kind} / {commands.recovery.attempt.key}</p><p>不会自动重放。请先查询当前事实，再明确决定是否重新操作；草稿保留。</p>
      <Button variant="secondary" isDisabled={commands.blocked || commands.recovery.phase === 'checking'} onPress={() => { void commands.verify(); }}>查询核对</Button>
      {commands.recovery.summary && <p role="status">{commands.recovery.summary}</p>}
      {commands.recovery.phase === 'checked' && <Button variant="secondary" isDisabled={commands.blocked} onPress={commands.allowManual}>我已核对，允许手动重新操作</Button>}
    </section>}
    {items.loading && <p role="status">加载配置列表…</p>}{items.error && <p role="alert">{items.error}</p>}
    {items.data && <section aria-label="配置项"><h2 className="text-lg font-semibold">配置项</h2><Table><Table.ScrollContainer role="region" aria-label="配置表格"><Table.Content aria-label="运行配置列表" className="min-w-[56rem]"><Table.Header>
      <Table.Column isRowHeader>配置键</Table.Column><Table.Column>名称</Table.Column><Table.Column>发布版本</Table.Column><Table.Column>草稿</Table.Column><Table.Column>更新时间</Table.Column><Table.Column>发布时间</Table.Column><Table.Column>归档时间</Table.Column><Table.Column>操作</Table.Column>
    </Table.Header><Table.Body renderEmptyState={() => '暂无运行配置'}>{items.data.map((row) => <Table.Row key={row.configKey} id={row.configKey}>
      <Table.Cell><code className="block w-40 break-all">{row.configKey}</code></Table.Cell><Table.Cell><span className="block w-48 break-all">{row.displayName}</span></Table.Cell><Table.Cell>{row.publishedVersion === null ? '未发布' : `v${row.publishedVersion}`}</Table.Cell><Table.Cell>{row.hasDraft ? '有草稿' : '无草稿'}</Table.Cell><Table.Cell>{row.updatedAt}</Table.Cell><Table.Cell>{row.publishedAt ?? '—'}</Table.Cell><Table.Cell>{row.retiredAt ?? '有效'}</Table.Cell>
      <Table.Cell focusMode="child"><Button size="sm" variant="secondary" isDisabled={selecting || commands.blocked || commands.pending} aria-label={`打开 ${row.configKey}`} onPress={() => { void openItem(row); }}>打开</Button></Table.Cell>
    </Table.Row>)}</Table.Body></Table.Content></Table.ScrollContainer></Table></section>}
    {selecting && <p role="status">读取配置详情…</p>}{selectedError && <p role="alert">{selectedError}</p>}
    {published && <section className="space-y-3" aria-label="已发布配置"><h2 className="text-lg font-semibold break-all">{published.name} / {published.key}</h2>
      {published.data ? <><p>已发布 v{published.data.version}</p><JsonEditor label="已发布配置" value={stringifyRuntime(published.data.payload, 2)} readOnly /></> : <p>该配置尚未发布或已归档；没有可用快照。</p>}
      <Button variant="secondary" isDisabled={selecting || commands.blocked} onPress={() => { void openItem({ configKey: published.key, displayName: published.name } as Item); }}>刷新已发布快照</Button>{snapshotNotice && <p role="status">{snapshotNotice}</p>}
    </section>}
    {detail && <section aria-label="配置详情" className="space-y-3"><h2 className="text-lg font-semibold break-all">{detail.displayName} / {detail.configKey}</h2><p>草稿版本 {detail.draftVersion}</p><p>{detail.publishedVersion === null ? '尚未发布' : `当前发布 v${detail.publishedVersion}`} · {detail.retiredAt ? '已归档' : '有效'}</p>
      <Button variant="secondary" isDisabled={commands.blocked || commands.pending || selecting} onPress={() => { void openItem(detail, true); }}>重新载入所选配置（放弃本地编辑）</Button>
      <JsonEditor label="Schema（创建后不可修改）" value={stringifyRuntime(detail.jsonSchema, 2)} readOnly />
      <JsonEditor label="草稿配置" value={payload} readOnly={Boolean(detail.retiredAt)} disabled={commands.pending || selecting} onChange={(value) => { if (!selectFlight.current) { commands.edited(); setPayload(value); } }} />
      {errors.payload && <p role="alert">{errors.payload}</p>}{errors.expectedDraftVersion && <p role="alert">{errors.expectedDraftVersion}</p>}
      <TextField value={comment} isDisabled={commands.pending || selecting} isInvalid={Boolean(errors.comment)} onChange={(value) => { if (!selectFlight.current) { commands.edited(); setComment(value); } }}><Label>发布说明</Label><Input maxLength={2000} /></TextField>{errors.comment && <p role="alert">{errors.comment}</p>}
      <div className="flex flex-wrap gap-3"><Button isDisabled={disabled || Boolean(detail.retiredAt)} onPress={() => { void saveDraft(); }}>保存草稿</Button><Button isDisabled={disabled || dirty || detail.draftPayload === null || Boolean(detail.retiredAt)} onPress={() => confirmItem('publish')}>发布新版本</Button><Button variant="secondary" isDisabled={disabled} onPress={() => confirmItem(detail.retiredAt ? 'restore' : 'retire')}>{detail.retiredAt ? '恢复配置' : '归档配置'}</Button></div>
      {dirty && <p>存在未保存编辑；发布按钮仅在草稿保存后开放。</p>}
      <section aria-label="发布历史" className="space-y-3"><h3 className="font-semibold">发布历史</h3><p>历史恢复会直接发布为更高新版本。</p><Button variant="secondary" isDisabled={commands.blocked || history.loading || selecting} onPress={() => { if (!selectFlight.current) void loadHistory(detail.configKey, selectedRequest.current); }}>刷新发布历史</Button>
        {history.loading && <p role="status">读取历史…</p>}{history.error && <p role="alert">{history.error}</p>}
        {history.data && <Table><Table.ScrollContainer role="region" aria-label="发布历史表格"><Table.Content aria-label="发布历史" className="min-w-[44rem]"><Table.Header><Table.Column isRowHeader>版本</Table.Column><Table.Column>说明</Table.Column><Table.Column>发布者</Table.Column><Table.Column>时间</Table.Column><Table.Column>内容与操作</Table.Column></Table.Header><Table.Body renderEmptyState={() => '尚无发布历史'}>{history.data.map((row) => <Table.Row key={row.version} id={row.version}><Table.Cell>v{row.version}</Table.Cell><Table.Cell><span className="block w-48 break-all">{row.comment ?? '—'}</span></Table.Cell><Table.Cell>{row.createdBy}</Table.Cell><Table.Cell>{row.createdAt}</Table.Cell><Table.Cell focusMode="child"><details><summary>查看 v{row.version} 内容</summary><JsonEditor label={`历史 v${row.version}`} value={stringifyRuntime(row.payload, 2)} readOnly /></details><Button size="sm" isDisabled={disabled || Boolean(detail.retiredAt) || dirty} onPress={() => confirmItem('rollback', row.version)}>恢复 v{row.version} 为新版本</Button></Table.Cell></Table.Row>)}</Table.Body></Table.Content></Table.ScrollContainer></Table>}
      </section>
    </section>}
    {writable && <><CreateConfig disabled={disabled} pending={commands.pending} replacesUnsavedDraft={dirty || comment.length > 0} errors={errors} edited={commands.edited} invalid={commands.invalid} create={create} preview={preview} />
      {secrets.loading && <p role="status">加载凭证元数据…</p>}{secrets.error && <p role="alert">{secrets.error}</p>}
      <SecretPanel secrets={secrets.data ?? []} disabled={disabled} pending={commands.pending} errors={errors} edited={commands.edited} invalid={commands.invalid} save={async (name, value) => {
        const result = await commands.run({ kind: 'secret-save', key: name }, `/secrets/${encodeURIComponent(name)}`, 'PUT', stringifyRuntime({ value }), '凭证加密保存已确认'); if (result) void loadSecrets(); return Boolean(result);
      }} lifecycle={(secret) => {
        const kind = secret.retiredAt ? 'secret-restore' : 'secret-retire'; setConfirmation({ attempt: { kind, key: secret.name }, path: `/secrets/${encodeURIComponent(secret.name)}/${secret.retiredAt ? 'restore' : 'retire'}`, title: secret.retiredAt ? '确认恢复凭证' : '确认归档凭证', message: '凭证明文不会回读；归档必须通过服务器引用检查，历史加密记录保留。', success: secret.retiredAt ? '凭证恢复已确认' : '凭证归档已确认' });
      }} /></>}
    <Modal.Backdrop isOpen={confirmation !== undefined} isDismissable={!commands.pending} isKeyboardDismissDisabled={commands.pending} onOpenChange={(open) => { if (!open && !commands.pending) setConfirmation(undefined); }}><Modal.Container size="lg"><Modal.Dialog><Modal.CloseTrigger aria-label="关闭确认" isDisabled={commands.pending} /><Modal.Header><Modal.Heading>{confirmation?.title}</Modal.Heading></Modal.Header><Modal.Body className="space-y-3"><p className="break-all">{confirmation?.attempt.key}</p><p>{confirmation?.message}</p>{commands.failure && <p role="alert">{commands.failure.message}</p>}<div className="flex flex-wrap gap-3"><Button isDisabled={disabled} onPress={() => { void executeConfirmation(); }}>确认执行</Button><Button variant="secondary" isDisabled={commands.pending} onPress={() => setConfirmation(undefined)}>取消</Button></div></Modal.Body></Modal.Dialog></Modal.Container></Modal.Backdrop>
  </div>;
}
