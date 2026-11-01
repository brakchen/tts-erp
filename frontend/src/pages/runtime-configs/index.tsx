import PageIntro from '../../shared/PageIntro';
import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Modal } from '@heroui/react';
import type { PageProps } from '../../shared/contracts';
import { decodeDetail, decodeItem, decodeList, decodeReview, decodeRevision, decodeSecret, record, type Detail, type Item, type Revision, type Secret } from './contracts';
import { parseObject, parseRuntime, stringifyRuntime } from './json';
import CreateConfig, { type CreateDraft } from './CreateConfig';
import SecretPanel from './SecretPanel';
import ConfigList from './ConfigList';
import ConfigWorkspace from './ConfigWorkspace';
import PublishReview from './PublishReview';
import RevisionHistory from './RevisionHistory';
import { useConfigWorkflow, type View } from './useConfigWorkflow';
import { useRuntimeCommands, type Attempt } from './useRuntimeCommands';

interface Confirmation { attempt: Attempt; path: string; title: string; message: string }
interface ReadOptions { retry?: () => void; failureMessage?: string }
export default function RuntimeConfigs({ bootstrap }: PageProps) {
  const writable = bootstrap.roleCapabilities?.readwrite === true;
  const commands = useRuntimeCommands(bootstrap.rootPath, writable);
  const [externalDirty, setExternalDirty] = useState(false);
  const { state, dispatch, dirty } = useConfigWorkflow(externalDirty);
  const [items, setItems] = useState<Item[]>([]);
  const [secrets, setSecrets] = useState<Secret[]>([]);
  const [history, setHistory] = useState<Revision[]>([]);
  const [configRetired, setConfigRetired] = useState(false);
  const [secretRetired, setSecretRetired] = useState(false);
  const [loading, setLoading] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation>();
  const [leave, setLeave] = useState<() => void>();
  const [conflict, setConflict] = useState(false);
  const [readRetry, setReadRetry] = useState<() => void>();
  const [savingToLeave, setSavingToLeave] = useState(false);
  const [refreshRequired, setRefreshRequired] = useState(false);
  const [formVersion, setFormVersion] = useState(0);
  const alive = useRef(true); const readFlight = useRef(false);
  const leaveFlight = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const firstView = useRef(true);
  const disabled = commands.disabled || loading || refreshRequired;
  const detail = state.detail;
  const errors = commands.failure?.fieldErrors ?? {};
  const leaveBusy = savingToLeave || commands.pending || loading;
  const path = (key: string) => `/items/${encodeURIComponent(key)}`;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (firstView.current) { firstView.current = false; return; }
    heading.current?.focus();
  }, [state.view, detail?.configKey]);

  async function reading<T>(task: () => Promise<T>, options: ReadOptions = {}): Promise<T | undefined> {
    if (readFlight.current || commands.pending || !commands.canRead()) return;
    readFlight.current = true; setLoading(true); setReadRetry(undefined); commands.clear(true);
    try { return await task(); }
    catch (error) {
      if (alive.current) {
        commands.reportRead(error, options.failureMessage);
        setReadRetry(() => options.retry ?? (() => { void reading(task, options); }));
      }
    }
    finally { readFlight.current = false; if (alive.current) setLoading(false); }
  }
  function edited() { setReadRetry(undefined); commands.edited(); }
  async function loadList(retired = configRetired) {
    await reading(async () => {
      const { data } = await commands.read<unknown>(`/items${retired ? '?includeRetired=true' : ''}`, {}, parseRuntime);
      const rows = decodeList(data, decodeItem); if (alive.current) setItems(rows);
    });
  }
  useEffect(() => { if (writable) void loadList(false); }, [bootstrap.rootPath, writable]);

  async function getDetail(key: string) {
    const { data } = await commands.read<unknown>(path(key), {}, parseRuntime);
    return decodeDetail(data, key);
  }
  async function openItem(item: Item) {
    await reading(async () => { const result = await getDetail(item.configKey); if (alive.current) { setConflict(false); setRefreshRequired(false); dispatch({ type: 'open', detail: result }); } });
  }
  async function loadSecrets(retired = secretRetired) {
    await reading(async () => {
      const { data } = await commands.read<unknown>(`/secrets${retired ? '?includeRetired=true' : ''}`, {}, parseRuntime);
      const rows = decodeList(data, decodeSecret); if (alive.current) setSecrets(rows);
    });
  }
  function navigate(view: View) {
    if (view === 'list' || view === 'create' || view === 'secrets') setRefreshRequired(false);
    setReadRetry(undefined); commands.clear(); setExternalDirty(false); setConflict(false); dispatch({ type: 'view', view });
    if (view === 'list') void loadList();
    if (view === 'secrets') void loadSecrets();
  }
  function protect(action: () => void) {
    if (commands.pending || loading) return;
    if (dirty || externalDirty) setLeave(() => action); else action();
  }
  async function saveDraft() {
    if (!detail || disabled || detail.retiredAt || conflict) return false;
    setReadRetry(undefined);
    let payload: Record<string, unknown>;
    try { payload = parseObject(state.text); } catch { commands.invalid('配置内容必须是有效 JSON 对象'); return false; }
    if (state.source !== null) {
      const valid = await reading(async () => {
        try {
          const { data } = await commands.read<unknown>(`${path(detail.configKey)}/preview`, { method: 'POST', body: stringifyRuntime({ expectedDraftVersion: detail.draftVersion, expectedPublishedVersion: detail.publishedVersion, payload, sourceRevisionVersion: state.source }) }, parseRuntime);
          decodeReview(data, detail, state.source); return true;
        } catch (error) { setConflict(isVersionConflict(error)); throw error; }
      });
      if (!valid) return false;
    }
    const result = await commands.run({ kind: 'draft', key: detail.configKey }, `${path(detail.configKey)}/draft`, 'PUT', stringifyRuntime({ expectedDraftVersion: detail.draftVersion, payload }), `草稿已保存，尚未发布；${detail.publishedVersion === null ? '当前尚无发布版本' : `当前仍使用 v${detail.publishedVersion}`}。`);
    if (result && result !== true && 'draftVersion' in result) { dispatch({ type: 'saved', detail: result }); return true; }
    return false;
  }
  async function check() {
    if (!detail || disabled || conflict) return;
    let payload: Record<string, unknown>;
    try { payload = parseObject(state.text); } catch { commands.invalid('配置内容必须是有效 JSON 对象'); return; }
    const frozenText = state.text; commands.clear();
    await reading(async () => {
      try {
        const { data } = await commands.read<unknown>(`${path(detail.configKey)}/preview`, { method: 'POST', body: stringifyRuntime({ expectedDraftVersion: detail.draftVersion, expectedPublishedVersion: detail.publishedVersion, payload, sourceRevisionVersion: state.source }) }, parseRuntime);
        const review = decodeReview(data, detail, state.source);
        if (alive.current) dispatch({ type: 'review', review, text: frozenText });
      } catch (error) { setConflict(isVersionConflict(error)); throw error; }
    });
  }
  function isVersionConflict(error: unknown) {
    return typeof error === 'object' && error !== null && 'status' in error && error.status === 409 && 'detail' in error && typeof error.detail === 'string' && /^(draft|published) version conflict;/.test(error.detail);
  }
  async function publish() {
    const review = state.review;
    if (!review || !detail || disabled || !review.hasChanges || state.reviewedText !== state.text) return;
    const result = await commands.run({ kind: 'publish', key: detail.configKey }, `${path(detail.configKey)}/publish`, 'POST', stringifyRuntime({ expectedDraftVersion: review.draftVersion, expectedPublishedVersion: review.publishedVersion, payload: parseObject(state.reviewedText), sourceRevisionVersion: review.sourceRevisionVersion, comment: state.comment || null }), '发布已确认');
    if (result && result !== true && 'publishedVersion' in result) {
      // The acknowledged write remains successful even if the following GET fails.
      const committed = { ...detail, publishedVersion: result.publishedVersion, publishedPayload: parseObject(review.candidateText), draftPayload: null, hasDraft: false };
      setRefreshRequired(true);
      dispatch({ type: 'open', detail: committed });
      await reading(async () => { const latest = await getDetail(detail.configKey); if (alive.current) { setRefreshRequired(false); dispatch({ type: 'open', detail: latest }); } }, { failureMessage: `发布为 v${result.publishedVersion} 已确认，最新详情加载失败；请重试读取。` });
    } else { dispatch({ type: 'invalidate' }); }
  }
  useEffect(() => {
    if (commands.failure?.message.startsWith('版本已变化')) { setConflict(true); dispatch({ type: 'invalidate' }); }
  }, [commands.failure]);

  async function loadLatest() {
    if (!detail) return;
    await reading(async () => { const latest = await getDetail(detail.configKey); if (alive.current) dispatch({ type: 'latest', detail: latest }); });
  }
  async function loadHistory() {
    if (!detail) return;
    await reading(async () => {
      const { data } = await commands.read<unknown>(`${path(detail.configKey)}/revisions`, {}, parseRuntime);
      const rows = decodeList(data, decodeRevision); if (alive.current) { setHistory(rows); dispatch({ type: 'view', view: 'history' }); }
    });
  }
  function useRevision(row: Revision) {
    if (!detail) return;
    setReadRetry(undefined);
    // Historical content may need repair. Validate provenance before any save
    // or publication, rather than blocking entry to the editor.
    dispatch({ type: 'edit', text: stringifyRuntime(row.payload, 2), source: row.version });
  }
  async function create(draft: CreateDraft) {
    setReadRetry(undefined);
    const result = await commands.run({ kind: 'create', key: draft.configKey }, '/items', 'POST', stringifyRuntime(draft), '配置已创建，尚未发布。');
    if (result && result !== true && 'draftVersion' in result) { setExternalDirty(false); dispatch({ type: 'open', detail: result, view: 'edit' }); return result; }
  }
  async function infer(payload: Record<string, unknown>, retry: () => void) {
    return reading(async () => {
      const { data } = await commands.read<unknown>('/schema/preview', { method: 'POST', body: stringifyRuntime({ payload }) }, parseRuntime);
      if (!record(data) || !record(data.jsonSchema)) throw Error('Invalid schema response');
      return data.jsonSchema;
    }, { retry });
  }
  function confirmLifecycle(secret?: Secret) {
    if (disabled) return;
    if (secret) {
      const restore = Boolean(secret.retiredAt);
      setConfirmation({ attempt: { kind: restore ? 'secret-restore' : 'secret-retire', key: secret.name }, path: `/secrets/${encodeURIComponent(secret.name)}/${restore ? 'restore' : 'retire'}`, title: restore ? '恢复凭证' : '归档凭证', message: '凭证明文不会回读；归档必须通过服务端引用检查，保留加密记录。' });
    } else if (detail) {
      const restore = Boolean(detail.retiredAt);
      setConfirmation({ attempt: { kind: restore ? 'restore' : 'retire', key: detail.configKey }, path: `${path(detail.configKey)}/${restore ? 'restore' : 'retire'}`, title: restore ? '重新启用配置' : '停用配置', message: restore ? `重新启用原发布版本${detail.publishedVersion === null ? '（此配置尚未发布）' : ` v${detail.publishedVersion}`}，不会发布草稿。服务器将检查凭证引用。` : '停用后不再出现在运行快照，历史记录保留。这不会停止正在运行的任务。' });
    }
  }
  async function executeLifecycle() {
    const current = confirmation; if (!current || disabled) return;
    setReadRetry(undefined);
    const result = await commands.run(current.attempt, current.path, 'POST', undefined, `${current.title}已确认`); setConfirmation(undefined);
    if (result) { if (current.attempt.kind.startsWith('secret-')) void loadSecrets(); else if (detail) void openItem(detail); }
  }
  async function allowManual() {
    const attempt = commands.recovery?.attempt;
    if (!attempt) return;
    setReadRetry(undefined);
    const sameConfig = detail?.configKey === attempt.key && attempt.kind !== 'create' && !attempt.kind.startsWith('secret-') && ['overview', 'edit', 'review', 'history'].includes(state.view);
    if (sameConfig) {
      await reading(async () => {
        const latest = await getDetail(attempt.key);
        if (alive.current) {
          commands.allowManual(); setConflict(false);
          if (state.view === 'edit' || state.view === 'review') { dispatch({ type: 'latest', detail: latest }); dispatch({ type: 'rebase' }); }
          else dispatch({ type: 'open', detail: latest, view: state.view });
        }
      });
    } else { commands.allowManual(); dispatch({ type: 'invalidate' }); }
  }
  async function saveAndLeave() {
    const action = leave;
    if (!action || leaveFlight.current || disabled || conflict) return;
    leaveFlight.current = true; setSavingToLeave(true);
    try {
      if (await saveDraft()) { dispatch({ type: 'discard' }); setLeave(undefined); action(); }
    } finally { leaveFlight.current = false; if (alive.current) setSavingToLeave(false); }
  }
  if (!writable) return <p>当前没有运行配置的读写权限，请联系管理员。</p>;
  const editing = state.view === 'edit';
  const viewTitles: Record<View, string> = { list: '配置列表', overview: '配置概览', edit: '修改配置', review: '检查发布', history: '发布历史', create: '新建配置', secrets: '凭证管理' };
  // Render feedback in the active interaction surface, never behind a modal.
  const feedback = <div className="space-y-3">
    {loading && <p role="status">正在读取或检查，请稍候…</p>}{commands.pending && <p role="status">正在提交，请等待结果，不要重复操作…</p>}{commands.blocked && <p role="status">请求过于频繁，等待服务端冷却后可手动重试。</p>}
    {commands.notice && <p role="status">{commands.notice}</p>}
    {commands.failure && <Alert status="danger"><Alert.Content><Alert.Title>操作未完成</Alert.Title><Alert.Description>{commands.failure.message}</Alert.Description></Alert.Content></Alert>}
    {Object.entries(errors).map(([field, message]) => <p role="alert" key={field}>{message}</p>)}
    {readRetry && <Button variant="secondary" isDisabled={commands.blocked || loading || commands.pending || savingToLeave} onPress={readRetry}>重试读取</Button>}
    {refreshRequired && <p role="status">发布已确认；请先重试读取最新详情，再继续修改。</p>}
    {commands.recovery && <section aria-label="提交结果核对" className="space-y-3"><p>提交结果尚未确认，不会自动重新提交。请先查询当前事实；你的输入已保留。</p><Button variant="secondary" isDisabled={commands.blocked || loading || savingToLeave || commands.recovery.phase === 'checking'} onPress={() => { setReadRetry(undefined); void commands.verify(); }}>查询核对</Button>{commands.recovery.summary && <p role="status" className="break-all">{commands.recovery.summary}</p>}{commands.recovery.phase === 'checked' && <Button variant="secondary" isDisabled={commands.blocked || loading || savingToLeave} onPress={() => { void allowManual(); }}>我已核对，重新检查内容</Button>}</section>}
  </div>;
  return <div className="erp-page">
    <PageIntro eyebrow="RUNTIME CONFIGURATION" title="让每次配置变更都有依据" description="查看已发布版本、编辑草稿，并在确认后发布或恢复历史。" />
    <div className="erp-toolbar">
    <h2 ref={heading} tabIndex={-1} className="text-lg font-semibold break-all">{detail && !['list', 'create', 'secrets'].includes(state.view) ? `${detail.displayName} / ${viewTitles[state.view]}` : viewTitles[state.view]}</h2>
      {state.view === 'list' ? <><p className="text-sm text-muted">管理业务使用的配置；修改确认发布后才会生效。</p><Button variant="secondary" isDisabled={disabled} onPress={() => protect(() => navigate('secrets'))}>管理凭证</Button><Button isDisabled={disabled} onPress={() => protect(() => navigate('create'))}>新建配置</Button></> : <Button variant="secondary" isDisabled={commands.pending || loading} onPress={() => protect(() => navigate(editing || state.view === 'review' || state.view === 'history' ? 'overview' : 'list'))}>{editing || state.view === 'review' || state.view === 'history' ? '返回配置概览' : '返回配置列表'}</Button>}
    {(state.view === 'list' || state.view === 'secrets') && <div className="erp-toolbar"><Button variant="secondary" isDisabled={disabled} onPress={() => { if (state.view === 'list') void loadList(); else void loadSecrets(); }}>刷新{state.view === 'list' ? '配置列表' : '凭证列表'}</Button><label><input type="checkbox" checked={state.view === 'list' ? configRetired : secretRetired} disabled={disabled} onChange={(e) => { const value = e.target.checked; if (state.view === 'list') { setConfigRetired(value); void loadList(value); } else { setSecretRetired(value); void loadSecrets(value); } }} /> 显示已{state.view === 'list' ? '停用配置' : '归档凭证'}</label></div>}
    </div>
    {detail && (state.view === 'review' || state.view === 'history') && <code className="block break-all text-muted">{detail.configKey}</code>}
    {!leave && feedback}
    {state.view === 'list' && <ConfigList items={items} disabled={disabled} open={(item) => { void openItem(item); }} />}
    {detail && (state.view === 'overview' || editing) && <ConfigWorkspace detail={detail} editing={editing} text={state.text} disabled={disabled} dirty={dirty} latest={state.latest} conflict={conflict}
      onText={(value) => { edited(); dispatch({ type: 'text', value }); }} edit={() => { setReadRetry(undefined); commands.clear(); dispatch({ type: 'edit' }); }} save={() => { void saveDraft(); }} check={() => { void check(); }} history={() => { void loadHistory(); }} lifecycle={() => protect(() => confirmLifecycle())}
      loadLatest={() => { void loadLatest(); }} rebase={() => { setReadRetry(undefined); commands.clear(); setConflict(false); dispatch({ type: 'rebase' }); }} />}
    {state.view === 'review' && state.review && <PublishReview review={state.review} comment={state.comment} disabled={disabled} changeComment={(value) => dispatch({ type: 'comment', value })} back={() => { setReadRetry(undefined); dispatch({ type: 'edit' }); }} publish={() => { void publish(); }} />}
    {state.view === 'history' && <RevisionHistory rows={history} disabled={disabled} useRevision={useRevision} />}
    {state.view === 'create' && <CreateConfig key={formVersion} disabled={disabled} errors={errors} edited={edited} invalid={commands.invalid} create={create} preview={infer} dirty={setExternalDirty} />}
    {state.view === 'secrets' && <SecretPanel key={formVersion} secrets={secrets} disabled={disabled} errors={errors} edited={edited} invalid={commands.invalid} dirty={setExternalDirty} lifecycle={(secret) => protect(() => confirmLifecycle(secret))} save={async (name, value) => { setReadRetry(undefined); const result = await commands.run({ kind: 'secret-save', key: name }, `/secrets/${encodeURIComponent(name)}`, 'PUT', stringifyRuntime({ value }), '凭证加密保存已确认'); if (result) { setExternalDirty(false); void loadSecrets(); } return Boolean(result); }} />}
    <Modal.Backdrop isOpen={Boolean(leave)} isDismissable={!leaveBusy} isKeyboardDismissDisabled={leaveBusy} onOpenChange={(open) => { if (!open && !leaveBusy && !leaveFlight.current) setLeave(undefined); }}>
      <Modal.Container><Modal.Dialog><Modal.Header><Modal.Heading>有未保存的输入</Modal.Heading></Modal.Header><Modal.Body className="space-y-3">
        <p>离开后这些本地输入将丢失。发布说明不会随草稿保存。</p>
        {externalDirty && <p>新建表单或凭证明文不会作为普通配置草稿保存。</p>}
        {leave && feedback}
        {conflict && <p>版本已变化，请选择“继续编辑”，查看最新内容后重新检查。</p>}
        <div className="flex flex-wrap gap-3">
          {!externalDirty && detail && <Button isDisabled={disabled || leaveBusy || conflict} onPress={() => { void saveAndLeave(); }}>暂存并离开</Button>}
          <Button variant="secondary" isDisabled={leaveBusy} onPress={() => {
            if (leaveFlight.current || leaveBusy) return;
            const action = leave; setLeave(undefined); setExternalDirty(false); setReadRetry(undefined); dispatch({ type: 'discard' }); setFormVersion((value) => value + 1); action?.();
          }}>放弃修改并离开</Button>
          <Button variant="secondary" isDisabled={leaveBusy} onPress={() => { if (!leaveFlight.current && !leaveBusy) setLeave(undefined); }}>继续编辑</Button>
        </div>
      </Modal.Body></Modal.Dialog></Modal.Container>
    </Modal.Backdrop>
    <Modal.Backdrop isOpen={Boolean(confirmation)} isDismissable={!commands.pending} isKeyboardDismissDisabled={commands.pending} onOpenChange={(open) => { if (!open && !commands.pending) setConfirmation(undefined); }}><Modal.Container><Modal.Dialog><Modal.Header><Modal.Heading>{confirmation?.title}</Modal.Heading></Modal.Header><Modal.Body className="space-y-3"><p className="break-all">{confirmation?.attempt.key}</p><p>{confirmation?.message}</p><Button isDisabled={disabled} onPress={() => { void executeLifecycle(); }}>确认执行</Button><Button variant="secondary" isDisabled={commands.pending} onPress={() => setConfirmation(undefined)}>取消</Button></Modal.Body></Modal.Dialog></Modal.Container></Modal.Backdrop>
  </div>;
}
