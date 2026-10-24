import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Label, Modal, Table, TextArea, TextField } from '@heroui/react';
import { ApiError, requestJson, scheduleReadDelay } from '../../shared/api/client';
import { commandFailure, type CommandFailure } from '../../shared/api/command-errors';
import type { PageProps } from '../../shared/contracts';
import ConfigForm, { type ConfigDraft } from './ConfigForm';
import { batchBody, configDetail, configInput, configList, configRow, count, importInputs, labels, object, parseIdentityJson, type ConfigInput, type ConfigRow } from './contracts';

const base = '/v2/intercept/configs';
const pageSize = 50;
const actions = { enable: '启用', disable: '禁用', delete: '删除' };
type BatchAction = keyof typeof actions;
type Filters = { domain: string; enabled: string; search: string };
type ListState = { loading: boolean; configs?: ConfigRow[]; total?: string; error?: string };
type Dialog = { kind: 'create' } | { kind: 'import' } | { kind: 'detail'; id: string; row?: ConfigRow; error?: string } | { kind: 'delete' | 'toggle'; row: ConfigRow } | { kind: 'batch'; action: BatchAction; rows: ConfigRow[] };
interface Attempt { kind: string; ids: string[]; inputs: ConfigInput[]; description: string }
interface Recovery { attempt: Attempt; phase: 'unverified' | 'checking' | 'checked' | 'failed'; facts?: ConfigRow[]; absent?: string[]; message?: string }
function readMessage(error: unknown) { return error instanceof ApiError ? `读取失败（HTTP ${error.status}）${error.status === 403 ? '：权限不足或安全检查未通过' : error.status === 401 ? '：登录已失效' : ''}` : '读取失败，请明确重试'; }
function Facts({ row }: { row: ConfigRow }) {
  return <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-2 break-all"><dt>ID</dt><dd>{row.id}</dd><dt>域名</dt><dd>{row.domain}</dd><dt>Endpoint</dt><dd>{row.endpoint}</dd><dt>模式</dt><dd>{row.mode === 'blacklist' ? '黑名单' : '白名单'}</dd><dt>状态</dt><dd>{row.enabled ? '启用' : '禁用'}</dd><dt>记录头</dt><dd>{row.capture_headers ? '是' : '否'}</dd><dt>记录体</dt><dd>{row.capture_body ? '是' : '否'}</dd><dt>描述</dt><dd>{row.description === null ? '—' : row.description === '' ? '（空字符串）' : row.description}</dd><dt>标签</dt><dd>{row.tags.join('、') || '—'}</dd><dt>创建时间</dt><dd>{row.created_at ?? '—'}</dd><dt>更新时间</dt><dd>{row.updated_at ?? '—'}</dd></dl>;
}

export default function InterceptConfigs({ bootstrap }: PageProps) {
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const [draftFilters, setDraftFilters] = useState<Filters>({ domain: '', enabled: '', search: '' });
  const [filters, setFilters] = useState(draftFilters);
  const [offset, setOffset] = useState(0); const [refresh, setRefresh] = useState(0);
  const [list, setList] = useState<ListState>({ loading: true });
  const [selected, setSelected] = useState<string[]>([]);
  const [dialog, setDialog] = useState<Dialog>();
  const [savedForm, setSavedForm] = useState<{ target: Extract<Dialog, { kind: 'create' | 'detail' }>; draft: ConfigDraft }>();
  const [importText, setImportText] = useState(''); const [fileReading, setFileReading] = useState(false);
  const [pending, setPending] = useState(false); const [exporting, setExporting] = useState(false);
  const [failure, setFailure] = useState<CommandFailure>(); const [recovery, setRecovery] = useState<Recovery>();
  const [readBlocked, setReadBlocked] = useState(false); const [commandBlocked, setCommandBlocked] = useState(false);
  const [notice, setNotice] = useState('');
  const mounted = useRef(true); const inFlight = useRef(false); const exportFlight = useRef(false);
  const recoveryGate = useRef<Recovery['phase'] | 'none'>('none');
  const readDeadline = useRef(0); const commandDeadline = useRef(0);
  const readTimer = useRef<(() => void) | undefined>(undefined); const commandTimer = useRef<(() => void) | undefined>(undefined);
  const detailController = useRef<AbortController | undefined>(undefined); const verification = useRef<AbortController | undefined>(undefined);
  const fileVersion = useRef(0);
  const blockRead = useCallback((error: unknown) => {
    const delay = error instanceof ApiError ? (error.retryAfter ?? 0) * 1000 : 0;
    if (delay <= 0) return;
    readDeadline.current = Math.max(readDeadline.current, performance.now() + delay); readTimer.current?.(); setReadBlocked(true);
    readTimer.current = scheduleReadDelay(() => { if (mounted.current) setReadBlocked(false); }, readDeadline.current - performance.now());
  }, []);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; readTimer.current?.(); commandTimer.current?.(); detailController.current?.abort(); verification.current?.abort(); fileVersion.current++; }; }, []);
  useEffect(() => {
    if (performance.now() < readDeadline.current) return;
    let current = true; const controller = new AbortController(); setList({ loading: true });
    const params = new URLSearchParams(); for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
    params.set('limit', String(pageSize)); params.set('offset', String(offset));
    requestJson<unknown>(bootstrap.rootPath, `${base}?${params}`, { signal: controller.signal }, parseIdentityJson)
      .then(({ data }) => { const result = configList(data); if (current) { setList({ ...result, loading: false }); setSelected([]); } })
      .catch((error: unknown) => { if (current) { blockRead(error); setList({ loading: false, error: readMessage(error) }); } });
    return () => { current = false; controller.abort(); };
  }, [bootstrap.rootPath, filters, offset, refresh, blockRead]);
  const needsCheck = recovery !== undefined && recovery.phase !== 'checked';
  const disabled = !canWrite || pending || commandBlocked || needsCheck;
  function mayWrite() { return canWrite && !inFlight.current && performance.now() >= commandDeadline.current && (recoveryGate.current === 'none' || recoveryGate.current === 'checked'); }
  function open(target: Dialog) {
    if (!mayWrite()) return;
    if (savedForm) { setNotice('已有未完成配置草稿，请先恢复或明确放弃。'); return; }
    setFailure(undefined); setRecovery(undefined); recoveryGate.current = 'none'; setNotice(''); setDialog(target);
  }
  function close() { if (!inFlight.current) { fileVersion.current++; setFileReading(false); detailController.current?.abort(); setDialog(undefined); } }
  function editField() { setFailure((previous) => previous ? { ...previous, fieldErrors: {} } : previous); }
  function saveDraft(draft: ConfigDraft) { editField(); if (dialog?.kind === 'create' || dialog?.kind === 'detail') setSavedForm({ target: dialog, draft }); }
  async function loadDetail(id: string) {
    if (inFlight.current || performance.now() < readDeadline.current || recoveryGate.current === 'checking' || (recoveryGate.current !== 'none' && recoveryGate.current !== 'checked')) return;
    if (savedForm) { setNotice('已有未完成配置草稿，请先恢复或明确放弃。'); return; }
    detailController.current?.abort(); const controller = new AbortController(); detailController.current = controller;
    setFailure(undefined); setRecovery(undefined); recoveryGate.current = 'none'; setDialog({ kind: 'detail', id });
    try {
      const { data } = await requestJson<unknown>(bootstrap.rootPath, `${base}/${id}`, { signal: controller.signal }, parseIdentityJson);
      const row = configDetail(data, id);
      if (mounted.current && !controller.signal.aborted) setDialog({ kind: 'detail', id, row });
    } catch (error: unknown) { if (mounted.current && !controller.signal.aborted) { blockRead(error); setDialog({ kind: 'detail', id, error: readMessage(error) }); } }
  }
  function rejected(error: unknown, attempt: Attempt) {
    const result = commandFailure(error, labels); setFailure(result);
    if (result.uncertain) { recoveryGate.current = 'unverified'; setRecovery({ attempt, phase: 'unverified' }); }
    if (result.retryAfter > 0) {
      commandDeadline.current = Math.max(commandDeadline.current, performance.now() + result.retryAfter * 1000); commandTimer.current?.(); setCommandBlocked(true);
      commandTimer.current = scheduleReadDelay(() => { if (mounted.current) setCommandBlocked(false); }, commandDeadline.current - performance.now());
    }
  }
  async function submit(input?: ConfigInput) {
    if (!dialog || !mayWrite() || fileReading) return;
    let path = base; let method = 'POST'; let body: string | undefined;
    let attempt: Attempt = { kind: dialog.kind, ids: [], inputs: [], description: '' };
    try {
      if (dialog.kind === 'create' || dialog.kind === 'detail') {
        if (!input || (dialog.kind === 'detail' && !dialog.row)) return;
        const value = configInput(input); body = JSON.stringify(value);
        if (dialog.kind === 'detail') { path = `${base}/${dialog.id}`; method = 'PUT'; attempt.ids = [dialog.id]; }
        attempt.inputs = [value]; attempt.description = `${value.domain} · ${value.endpoint}`;
      } else if (dialog.kind === 'import') {
        const inputs = importInputs(importText); body = JSON.stringify({ configs: inputs }); path = `${base}/import`; attempt.inputs = inputs;
        attempt.description = `导入 ${inputs.length} 条配置`;
      } else if (dialog.kind === 'batch') {
        attempt.ids = dialog.rows.map((row) => row.id); body = batchBody(dialog.action, attempt.ids); path = `${base}/batch`;
        attempt.description = `批量${actions[dialog.action]}：${attempt.ids.join('、')}`;
      } else {
        attempt.ids = [dialog.row.id]; attempt.description = `${dialog.row.id} · ${dialog.row.domain} · ${dialog.row.endpoint}`;
        path = `${base}/${dialog.row.id}`;
        if (dialog.kind === 'delete') method = 'DELETE';
        else { path += '/toggle'; method = 'PATCH'; body = JSON.stringify({ enabled: !dialog.row.enabled }); }
      }
    } catch {
      setFailure({ message: dialog.kind === 'import' ? '导入格式无效：需要非空数组或 { configs: [...] }，每项含合法域名、Endpoint、模式和开关；原文已保留。' : '请检查域名与 Endpoint 长度及配置字段', fieldErrors: {}, uncertain: false, retryAfter: 0 }); return;
    }
    const target = dialog; inFlight.current = true; setPending(true); setFailure(undefined); setNotice('');
    try {
      const { data } = await requestJson<unknown>(bootstrap.rootPath, path, { method, body }, parseIdentityJson);
      let message = '';
      if (target.kind === 'delete') { if (object(data).success !== true) throw new Error('删除响应不符'); message = '配置删除已确认'; }
      else if (target.kind === 'batch') { message = `批量${actions[target.action]}已确认：影响 ${count(object(data).affected)} 条`; }
      else if (target.kind === 'import') {
        const result = object(data); const imported = count(result.imported); const skipped = count(result.skipped);
        if (!Array.isArray(result.errors)) throw new Error('导入响应不符');
        const indices = result.errors.map((item) => count(object(item).index));
        message = `导入已确认：成功 ${imported} 条，跳过 ${skipped} 条，错误 ${indices.length} 条${indices.length ? `（项目索引：${indices.join('、')}；为避免泄露，不展示原始服务器错误）` : ''}`;
      } else {
        const row = configDetail(data, target.kind === 'create' ? undefined : attempt.ids[0]);
        if (target.kind === 'toggle' && row.enabled !== !target.row.enabled) throw new Error('启停响应不符');
        if (attempt.inputs[0] && (row.domain !== attempt.inputs[0].domain || row.endpoint !== attempt.inputs[0].endpoint)) throw new Error('保存响应身份不符');
        message = target.kind === 'toggle' ? '配置启停已确认' : '配置保存已确认';
      }
      if (mounted.current) { setDialog(undefined); if (target.kind === 'create' || target.kind === 'detail') setSavedForm(undefined); setFailure(undefined); setRecovery(undefined); recoveryGate.current = 'none'; setNotice(message); setRefresh((value) => value + 1); }
    } catch (error: unknown) { if (mounted.current) rejected(error, attempt); }
    finally { inFlight.current = false; if (mounted.current) setPending(false); }
  }
  async function verifyAttempt() {
    if (!recovery || recoveryGate.current === 'checking' || inFlight.current || performance.now() < readDeadline.current) return;
    const attempt = recovery.attempt; const controller = new AbortController(); verification.current = controller;
    recoveryGate.current = 'checking'; setRecovery({ attempt, phase: 'checking' });
    try {
      const facts: ConfigRow[] = []; const absent: string[] = [];
      // A bounded current-facts lookup, never a commit receipt or automatic replay.
      for (const id of attempt.ids.slice(0, pageSize)) {
        try { const { data } = await requestJson<unknown>(bootstrap.rootPath, `${base}/${id}`, { signal: controller.signal }, parseIdentityJson); facts.push(configDetail(data, id)); }
        catch (error: unknown) { if (error instanceof ApiError && error.status === 404) absent.push(id); else throw error; }
      }
      const domains = [...new Set(attempt.inputs.map((item) => item.domain))]; let truncated = attempt.ids.length > pageSize || domains.length > 20;
      if (!attempt.ids.length) for (const domain of domains.slice(0, 20)) {
        const params = new URLSearchParams({ domain, limit: '500', offset: '0' });
        const { data } = await requestJson<unknown>(bootstrap.rootPath, `${base}?${params}`, { signal: controller.signal }, parseIdentityJson); const result = configList(data);
        if (BigInt(result.total) > BigInt(result.configs.length)) truncated = true;
        facts.push(...result.configs.filter((row) => attempt.inputs.some((input) => input.domain === row.domain && input.endpoint === row.endpoint)));
      }
      if (mounted.current && !controller.signal.aborted) {
        recoveryGate.current = 'checked'; setRecovery({ attempt, phase: 'checked', facts, absent,
          message: `当前事实已读取${truncated ? '（有界查询，未覆盖全部候选）' : ''}。${facts.length ? '匹配记录见下方。' : '本次查询未找到匹配记录。'}当前存在或缺席均不能证明原提交结果，也不证明回滚；不会自动重试。可保留草稿并明确决定是否再次提交。` });
      }
    } catch (error: unknown) { if (mounted.current && !controller.signal.aborted) { blockRead(error); recoveryGate.current = 'failed'; setRecovery({ attempt, phase: 'failed', message: `${readMessage(error)}；核对未完成，草稿与目标仍保留。` }); } }
    finally { if (verification.current === controller) verification.current = undefined; }
  }
  async function exportConfigs() {
    if (exportFlight.current || performance.now() < readDeadline.current) return;
    exportFlight.current = true; setExporting(true);
    try {
      const { data } = await requestJson<{ raw: string; parsed: unknown }>(bootstrap.rootPath, `${base}/export`, {}, (raw) => ({ raw, parsed: parseIdentityJson(raw) }));
      const value = object(data.parsed); if (!Array.isArray(value.configs)) throw new Error('导出列表缺失'); value.configs.forEach(configRow);
      if (!mounted.current) return;
      const url = URL.createObjectURL(new Blob([data.raw], { type: 'application/json' }));
      try { const link = document.createElement('a'); link.href = url; link.download = `intercept-configs-${new Date().toISOString().slice(0, 10)}.json`; document.body.appendChild(link); link.click(); link.remove(); }
      finally { URL.revokeObjectURL(url); }
      setNotice('全部配置已导出（不受当前筛选或分页限制）');
    } catch (error: unknown) { if (mounted.current) { blockRead(error); setNotice(`导出未完成：${readMessage(error)}`); } }
    finally { exportFlight.current = false; if (mounted.current) setExporting(false); }
  }
  const alert = failure && <Alert status="danger"><Alert.Content><Alert.Title>提交未完成</Alert.Title><Alert.Description>{failure.message}</Alert.Description>{Object.entries(failure.fieldErrors).filter(([key]) => !['domain', 'endpoint', 'description', 'tags'].includes(key)).map(([key, value]) => <p key={key}>{value}</p>)}</Alert.Content></Alert>;
  const check = recovery && <section aria-label="提交结果核对" className="space-y-3"><p className="break-all">捕获目标：{recovery.attempt.description}</p><p>先读取当前事实，再由你明确决定下一步；GET 不是原命令的提交证明。</p>
    <Button variant="secondary" isDisabled={recovery.phase === 'checking' || readBlocked} onPress={() => { void verifyAttempt(); }}>查询核对</Button>
    {recovery.phase === 'checking' && <p role="status">核对中…</p>}{recovery.message && <p role="status">{recovery.message}</p>}
    {recovery.absent?.length ? <p>当前未找到 ID：{recovery.absent.join('、')}</p> : null}
    {recovery.facts?.map((row) => <section key={row.id} aria-label={`当前配置 ${row.id}`}><h3>当前公开配置</h3><Facts row={row} /></section>)}
  </section>;
  const rows = list.configs ?? []; const selectedRows = rows.filter((row) => selected.includes(row.id));
  const nextDisabled = list.total === undefined || BigInt(offset + pageSize) >= BigInt(list.total) || offset > Number.MAX_SAFE_INTEGER - pageSize;
  return <div className="space-y-4">
    <form aria-label="配置筛选" className="flex flex-wrap items-end gap-3" onSubmit={(event) => { event.preventDefault(); if (performance.now() < readDeadline.current) return; setFilters({ ...draftFilters, domain: draftFilters.domain.trim(), search: draftFilters.search.trim() }); setOffset(0); setRefresh((value) => value + 1); }}>
      <TextField value={draftFilters.domain} onChange={(value) => setDraftFilters((previous) => ({ ...previous, domain: value }))}><Label>筛选域名</Label><Input /></TextField>
      <label>筛选状态 <select value={draftFilters.enabled} onChange={(event) => setDraftFilters((previous) => ({ ...previous, enabled: event.target.value }))}><option value="">全部</option><option value="true">启用</option><option value="false">禁用</option></select></label>
      <TextField value={draftFilters.search} onChange={(value) => setDraftFilters((previous) => ({ ...previous, search: value }))}><Label>搜索</Label><Input /></TextField><Button type="submit" isDisabled={readBlocked}>查询配置</Button>
      <Button variant="secondary" isDisabled={readBlocked || list.loading} onPress={() => setRefresh((value) => value + 1)}>刷新配置</Button>
    </form>
    <p>域名按服务端精确筛选。保留旧版 search 参数；当前接口未实现搜索，不保证搜索条件生效。排序沿用服务端 ID 倒序。</p>
    <div className="flex flex-wrap gap-3"><Button isDisabled={disabled} onPress={() => open({ kind: 'create' })}>新增配置</Button><Button variant="secondary" isDisabled={disabled} onPress={() => open({ kind: 'import' })}>导入配置</Button><Button variant="secondary" isDisabled={readBlocked || exporting} onPress={() => { void exportConfigs(); }}>{exporting ? '导出中…' : '导出全部配置'}</Button></div>
    {!dialog && savedForm && <div className="flex flex-wrap gap-3"><p>有未完成配置草稿，切换配置不会覆盖输入。</p><Button variant="secondary" isDisabled={pending} onPress={() => setDialog(savedForm.target)}>恢复配置草稿</Button><Button variant="danger" isDisabled={pending || needsCheck} onPress={() => setSavedForm(undefined)}>放弃配置草稿</Button></div>}
    {!canWrite && <p>配置列表、详情、导出及变更均需 readwrite 权限和服务端授权；当前权限提示不允许变更。</p>}
    <p role="status">{list.total === undefined ? '共 — 条' : `共 ${list.total} 条`} · 已选 {selectedRows.length} 条</p>
    <div className="flex flex-wrap gap-3">{(Object.keys(actions) as BatchAction[]).map((action) => <Button key={action} variant={action === 'delete' ? 'danger' : 'secondary'} isDisabled={disabled || !selectedRows.length} onPress={() => open({ kind: 'batch', action, rows: [...selectedRows] })}>批量{actions[action]}</Button>)}</div>
    {pending && <p role="status">提交中，请等待确认…</p>}{commandBlocked && <p role="status">提交冷却中；请等待服务端指定时间，届时仅允许明确重试。</p>}{readBlocked && <p role="status">读取冷却中；等待结束后请明确查询，不会自动重试。</p>}
    {notice && <p role="status">{notice}</p>}{!dialog && <>{alert}{check}</>}
    {list.loading && <p role="status">加载配置中…</p>}{list.error && <Alert status="danger"><Alert.Content><Alert.Title>列表不可用</Alert.Title><Alert.Description>{list.error}</Alert.Description><Button isDisabled={readBlocked} onPress={() => setRefresh((value) => value + 1)}>重试列表</Button></Alert.Content></Alert>}
    {list.configs && <><p>字段较多，可在表格区域横向滚动。</p><Table><Table.ScrollContainer role="region" aria-label="拦截配置表格"><Table.Content aria-label="拦截配置" className="min-w-[78rem]"><Table.Header>
      {['选择', 'ID', '域名', 'Endpoint', '模式', '状态', '记录头', '记录体', '描述', '标签', '操作'].map((title) => <Table.Column key={title} isRowHeader={title === 'ID'}>{title === '选择' ? <label><input type="checkbox" aria-label="选择本页全部" checked={rows.length > 0 && selectedRows.length === rows.length} onChange={(event) => setSelected(event.target.checked ? rows.map((row) => row.id) : [])} /> 本页</label> : title}</Table.Column>)}
    </Table.Header><Table.Body renderEmptyState={() => '暂无配置'}>{rows.map((row) => <Table.Row key={row.id} id={row.id}>
      <Table.Cell focusMode="child"><input type="checkbox" aria-label={`选择配置 ${row.id}`} checked={selected.includes(row.id)} onChange={(event) => setSelected((previous) => event.target.checked ? [...previous, row.id] : previous.filter((id) => id !== row.id))} /></Table.Cell>
      <Table.Cell>{row.id}</Table.Cell><Table.Cell><span className="block w-48 break-all">{row.domain}</span></Table.Cell><Table.Cell><span className="block w-48 break-all">{row.endpoint}</span></Table.Cell><Table.Cell>{row.mode === 'blacklist' ? '黑名单' : '白名单'}</Table.Cell><Table.Cell>{row.enabled ? '启用' : '禁用'}</Table.Cell><Table.Cell>{row.capture_headers ? '是' : '否'}</Table.Cell><Table.Cell>{row.capture_body ? '是' : '否'}</Table.Cell><Table.Cell><span className="block w-48 whitespace-pre-wrap break-all">{row.description ?? '—'}</span></Table.Cell><Table.Cell><span className="block w-36 break-all">{row.tags.join('、') || '—'}</span></Table.Cell>
      <Table.Cell focusMode="child"><div className="flex flex-wrap gap-2"><Button size="sm" variant="secondary" isDisabled={pending || needsCheck || readBlocked} onPress={() => { void loadDetail(row.id); }}>{canWrite ? '详情与编辑' : '查询详情（需 readwrite 授权）'}</Button><Button size="sm" variant="secondary" isDisabled={disabled} onPress={() => open({ kind: 'toggle', row })}>{row.enabled ? '禁用配置' : '启用配置'}</Button><Button size="sm" variant="danger" isDisabled={disabled} onPress={() => open({ kind: 'delete', row })}>删除配置</Button></div></Table.Cell>
    </Table.Row>)}</Table.Body></Table.Content></Table.ScrollContainer></Table></>}
    <div className="flex flex-wrap items-center gap-3"><Button variant="secondary" isDisabled={list.loading || readBlocked || offset === 0} onPress={() => setOffset(Math.max(0, offset - pageSize))}>上一页</Button><span>第 {Math.floor(offset / pageSize) + 1} 页 / {list.total === undefined ? '—' : String((BigInt(list.total) + 49n) / 50n || 1n)} 页</span><Button variant="secondary" isDisabled={list.loading || readBlocked || nextDisabled} onPress={() => setOffset(offset + pageSize)}>下一页</Button></div>
    <Modal.Backdrop isOpen={dialog !== undefined} isDismissable={!pending} isKeyboardDismissDisabled={pending} onOpenChange={(isOpen) => { if (!isOpen) close(); }}><Modal.Container size="lg" scroll="inside"><Modal.Dialog>
      <Modal.CloseTrigger aria-label="关闭配置窗口" isDisabled={pending} /><Modal.Header><Modal.Heading>{dialog?.kind === 'create' ? '新增拦截配置' : dialog?.kind === 'detail' ? '配置详情' : dialog?.kind === 'import' ? '导入拦截配置' : dialog?.kind === 'batch' ? `确认批量${actions[dialog.action]}` : dialog?.kind === 'toggle' ? '确认切换状态' : '确认删除配置'}</Modal.Heading></Modal.Header>
      <Modal.Body className="space-y-4">{alert}{check}
        {dialog?.kind === 'create' && <ConfigForm saved={savedForm?.target.kind === 'create' ? savedForm.draft : undefined} pending={pending} blocked={disabled} errors={failure?.fieldErrors ?? {}} retry={recovery?.phase === 'checked'} onEdit={saveDraft} onSubmit={(value) => { void submit(value); }} onCancel={close} />}
        {dialog?.kind === 'detail' && <>{!dialog.row && !dialog.error && <p role="status">加载详情中…</p>}{dialog.error && <><p role="alert">{dialog.error}</p><Button isDisabled={readBlocked} onPress={() => { void loadDetail(dialog.id); }}>重试详情</Button></>}{dialog.row && <><Facts row={dialog.row} />{canWrite && <ConfigForm key={dialog.id} initial={dialog.row} saved={savedForm?.target.kind === 'detail' && savedForm.target.id === dialog.id ? savedForm.draft : undefined} pending={pending} blocked={disabled} errors={failure?.fieldErrors ?? {}} retry={recovery?.phase === 'checked'} onEdit={saveDraft} onSubmit={(value) => { void submit(value); }} onCancel={close} />}</>}</>}
        {dialog?.kind === 'import' && <form aria-label="导入表单" className="space-y-3" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
          <p>接受 JSON 数组或导出的 {'{ configs: [...] }'}；重复域名与 Endpoint 由服务端跳过。支持选择文件后编辑原文。</p>
          <label className="block">选择 JSON 文件<input type="file" accept=".json,application/json" disabled={pending || fileReading} onChange={(event) => { const file = event.target.files?.[0]; if (!file) return; const version = ++fileVersion.current; setFileReading(true); void file.text().then((text) => { if (mounted.current && version === fileVersion.current) { setImportText(text); editField(); } }).catch(() => { if (mounted.current && version === fileVersion.current) setFailure({ message: '文件读取失败，原文已保留', fieldErrors: {}, uncertain: false, retryAfter: 0 }); }).finally(() => { if (mounted.current && version === fileVersion.current) setFileReading(false); }); }} /></label>
          <TextField value={importText} isDisabled={pending || fileReading} onChange={(value) => { setImportText(value); editField(); }}><Label>导入 JSON</Label><TextArea rows={12} /></TextField>
          <Button type="submit" isDisabled={disabled || fileReading}>{recovery?.phase === 'checked' ? '核对后明确重新导入' : '确认导入'}</Button><Button variant="secondary" isDisabled={pending} onPress={close}>取消</Button>
        </form>}
        {dialog && ['delete', 'toggle', 'batch'].includes(dialog.kind) && <><p>以下目标已固定，不随列表刷新或选择变化。服务端权限、CSRF 与删除安全检查保持权威。</p><ul className="space-y-2 break-all">{(dialog.kind === 'batch' ? dialog.rows : dialog.kind === 'delete' || dialog.kind === 'toggle' ? [dialog.row] : []).map((row) => <li key={row.id}>#{row.id} · {row.domain} · {row.endpoint} · 当前{row.enabled ? '启用' : '禁用'}</li>)}</ul>
          <p>{dialog.kind === 'delete' || (dialog.kind === 'batch' && dialog.action === 'delete') ? '确认永久删除这些配置？不会自动重试。' : dialog.kind === 'toggle' ? `确认${dialog.row.enabled ? '禁用' : '启用'}此配置？` : dialog.kind === 'batch' ? `确认批量${actions[dialog.action]}？` : ''}</p>
          <div className="flex flex-wrap gap-3"><Button variant={dialog.kind === 'delete' || (dialog.kind === 'batch' && dialog.action === 'delete') ? 'danger' : 'primary'} isDisabled={disabled} onPress={() => { void submit(); }}>{recovery?.phase === 'checked' ? '核对后明确再次执行' : '确认执行'}</Button><Button variant="secondary" isDisabled={pending} onPress={close}>取消</Button></div>
        </>}
      </Modal.Body>
    </Modal.Dialog></Modal.Container></Modal.Backdrop>
  </div>;
}
