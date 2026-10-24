import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Label, ListBox, Modal, Select, Tabs, TextField } from '@heroui/react';
import type { PageProps } from '../../shared/contracts';
import { catalog as decodeCatalog, detail as decodeDetail, sessions as decodeSessions, users as decodeUsers, userPath, type Catalog, type CommandKind, type Role, type Session, type User, type UserDetail } from './contracts';
import { AccountForm, initialDraft, type Draft } from './AccountForm';
import { RolesTable, SessionsTable, UsersTable } from './Tables';
import { readError, useCommands, type Command } from './useCommands';

type Dialog = { kind: CommandKind | 'detail' | 'sessions'; user?: User; role?: Role };
interface ReadState<T> { loading: boolean; data?: T; error?: string }
const emptyCatalog: Catalog = { roles: [], allPermissions: [] };
export default function Users({ bootstrap }: PageProps) {
  const canManage = bootstrap.roleCapabilities?.admin === true;
  const command = useCommands(bootstrap.rootPath, canManage);
  const [people, setPeople] = useState<ReadState<User[]>>({ loading: true });
  const [catalog, setCatalog] = useState<ReadState<Catalog>>({ loading: true });
  const [publicUser, setPublicUser] = useState<ReadState<UserDetail>>({ loading: false });
  const [sessionList, setSessionList] = useState<ReadState<Session[]>>({ loading: false });
  const [dialog, setDialog] = useState<Dialog>();
  const [isOpen, setOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(initialDraft());
  const [revoke, setRevoke] = useState<string | 'all'>();
  const [verificationSummary, setVerificationSummary] = useState('');
  const [keyword, setKeyword] = useState(''), [status, setStatus] = useState('');
  const alive = useRef(true), listVersion = useRef(0), catalogVersion = useRef(0), dialogVersion = useRef(0);
  async function loadPeople() {
    if (!command.readyRead()) return;
    const version = ++listVersion.current; setPeople({ loading: true });
    try { const data = await command.read('/v2/users', decodeUsers); if (alive.current && version === listVersion.current) setPeople({ loading: false, data }); }
    catch (error) { if (alive.current && version === listVersion.current) setPeople({ loading: false, error: readError(error) }); }
  }
  async function loadCatalog() {
    if (!command.readyRead()) return;
    const version = ++catalogVersion.current; setCatalog({ loading: true });
    try { const data = await command.read('/v2/roles', decodeCatalog); if (alive.current && version === catalogVersion.current) setCatalog({ loading: false, data }); }
    catch (error) { if (alive.current && version === catalogVersion.current) setCatalog({ loading: false, error: readError(error) }); }
  }
  async function loadDialog(target: Dialog) {
    if (!target.user || !command.readyRead()) return;
    const version = ++dialogVersion.current;
    if (target.kind === 'sessions') {
      setSessionList({ loading: true });
      try { const data = await command.read(`${userPath(target.user.id)}/sessions`, decodeSessions); if (alive.current && version === dialogVersion.current) setSessionList({ loading: false, data }); }
      catch (error) { if (alive.current && version === dialogVersion.current) setSessionList({ loading: false, error: readError(error) }); }
    } else {
      setPublicUser({ loading: true });
      try { const data = await command.read(userPath(target.user.id), decodeDetail); if (data.id !== target.user.id) throw new Error('身份不匹配'); if (alive.current && version === dialogVersion.current) setPublicUser({ loading: false, data }); }
      catch (error) { if (alive.current && version === dialogVersion.current) setPublicUser({ loading: false, error: readError(error) }); }
    }
  }
  useEffect(() => { alive.current = true; void loadPeople(); void loadCatalog(); return () => { alive.current = false; }; }, [bootstrap.rootPath]); // Reads are explicitly refreshed, never automatically replayed.
  const unresolved = command.phase !== 'none';
  const catalogReady = catalog.data !== undefined && !catalog.loading && !catalog.error;
  function open(target: Dialog) {
    if (command.pending || unresolved || command.cooldown) return;
    if (!['detail', 'sessions'].includes(target.kind) && (!canManage || !command.reset())) return;
    setDialog(target); setDraft(initialDraft(target.user, target.role)); setRevoke(undefined); setVerificationSummary(''); setOpen(true);
    setPublicUser({ loading: false }); setSessionList({ loading: false });
    if (target.kind === 'detail' || target.kind === 'sessions') void loadDialog(target);
  }
  function close() {
    if (command.pending || command.phase === 'checking') return;
    setOpen(false); dialogVersion.current++;
    // An uncertain command retains its draft in memory until explicitly resolved.
    if (command.phase === 'none') { setDraft(initialDraft()); setDialog(undefined); setRevoke(undefined); }
  }
  function makeCommand(): Command | undefined {
    if (!dialog) return;
    const path = dialog.user ? userPath(dialog.user.id) : '';
    switch (dialog.kind) {
      case 'createUser': return { path: '/v2/users', method: 'POST', kind: dialog.kind, body: { username: draft.username.trim(), displayName: draft.displayName.trim(), password: draft.password, roles: draft.roles } };
      case 'displayName': return { path, method: 'PATCH', kind: dialog.kind, body: { displayName: draft.displayName.trim() } };
      case 'roles': return { path, method: 'PATCH', kind: dialog.kind, body: { roles: draft.roles } };
      case 'status': return { path, method: 'PATCH', kind: dialog.kind, body: { status: dialog.user?.status === 'active' ? 'disabled' : 'active' } };
      case 'password': return { path: `${path}/password`, method: 'POST', kind: dialog.kind, body: { newPassword: draft.newPassword } };
      case 'createRole': return { path: '/v2/roles', method: 'POST', kind: dialog.kind, body: { code: draft.code.trim().toLowerCase(), name: draft.name.trim(), apiTier: draft.apiTier, permissions: draft.permissions } };
      case 'editRole': return { path: `/v2/roles/${encodeURIComponent(dialog.role!.code)}`, method: 'PATCH', kind: dialog.kind, body: { name: draft.name.trim(), ...(!dialog.role?.isBuiltin ? { apiTier: draft.apiTier } : {}), permissions: draft.permissions } };
      case 'deleteRole': return { path: `/v2/roles/${encodeURIComponent(dialog.role!.code)}`, method: 'DELETE', kind: dialog.kind };
      case 'sessions': if (revoke) return { path: `${path}/sessions${revoke === 'all' ? '' : `/${encodeURIComponent(revoke)}`}`, method: 'DELETE', kind: 'revoke' }; return;
    }
  }
  async function submit() {
    if (!catalogReady && dialog && ['createUser', 'roles', 'createRole', 'editRole'].includes(dialog.kind)) return;
    const request = makeCommand(); if (!request) return;
    if (await command.submit(request)) {
      if (!alive.current) return;
      setDraft(initialDraft());
      if (dialog?.kind === 'sessions') { setRevoke(undefined); void loadDialog(dialog); }
      else { setOpen(false); setDialog(undefined); }
      void loadPeople(); void loadCatalog();
    }
  }
  async function verify() {
    if (!dialog) return;
    await command.verify(async () => {
      if (dialog.user) {
        const result = await command.read(userPath(dialog.user.id), decodeDetail);
        if (result.id !== dialog.user.id) throw new Error('身份不匹配');
        if (alive.current) setPublicUser({ loading: false, data: result });
        if (dialog.kind === 'sessions') { const data = await command.read(`${userPath(dialog.user.id)}/sessions`, decodeSessions); if (alive.current) setSessionList({ loading: false, data }); }
      } else if (dialog.kind === 'createUser') {
        const data = await command.read('/v2/users', decodeUsers);
        if (alive.current) {
          setPeople({ loading: false, data });
          const current = data.find((row) => row.username === draft.username.trim().toLowerCase());
          setVerificationSummary(current ? `当前公开用户：${current.username} · ID ${current.id} · ${current.status}` : '当前公开列表未找到该用户名；不能据此断定提交未生效。');
        }
      } else {
        const data = await command.read('/v2/roles', decodeCatalog);
        if (alive.current) {
          setCatalog({ loading: false, data });
          const current = data.roles.find((row) => row.code === (dialog.role?.code ?? draft.code.trim().toLowerCase()));
          setVerificationSummary(current ? `当前公开角色：${current.code} · ${current.name} · ${current.apiTier} · ${current.permissions.join('、') || '无页面权限'}` : '当前公开列表未找到该角色代码；不能据此断定提交未生效。');
        }
      }
    });
  }
  const recovery = command.failure && <div className="space-y-3">
    <Alert status="danger"><Alert.Content><Alert.Title>提交未完成</Alert.Title><Alert.Description>{command.failure.message}</Alert.Description></Alert.Content></Alert>
    {command.failure.uncertain && <section aria-label="提交结果核对" className="space-y-2">
      <p>不会自动重放。公开状态不能证明原命令成功、失败或回滚，也不能验证密码；请自行核对后明确决定。</p>
      <Button variant="secondary" isDisabled={command.cooldown || command.phase === 'checking'} onPress={() => { void verify(); }}>查询公开状态</Button>
      {command.phase === 'checking' && <p role="status">查询中…</p>}{command.phase === 'failed' && <p role="status">查询未完成，请手动重试查询；仍不能提交。</p>}
      {['checked', 'allowed'].includes(command.phase) && <><p role="status">公开状态已读取；不证明原提交结果。</p>{verificationSummary && <p className="break-all">{verificationSummary}</p>}{publicUser.data && <p>当前用户：{publicUser.data.username} · {publicUser.data.displayName} · {publicUser.data.status} · {publicUser.data.roles.join('、')}</p>}{command.phase === 'checked' && <Button isDisabled={command.cooldown} onPress={command.allowRetry}>我已核对，允许手动重试</Button>}</>}
    </section>}
  </div>;
  const title = dialog?.kind === 'createUser' ? '新建用户' : dialog?.kind === 'displayName' ? '编辑显示名' : dialog?.kind === 'roles' ? '分配角色' : dialog?.kind === 'password' ? '重置密码' : dialog?.kind === 'sessions' ? '会话管理' : dialog?.kind === 'detail' ? '用户详情' : dialog?.kind === 'status' ? '确认更改用户状态' : dialog?.kind === 'createRole' ? '新建角色' : dialog?.kind === 'editRole' ? '编辑角色' : '确认删除角色';
  const blockActions = command.pending || unresolved || command.cooldown;
  const formKind = dialog && ['createUser', 'displayName', 'roles', 'password', 'createRole', 'editRole'].includes(dialog.kind);
  return <div className="space-y-4 min-w-0">
    <p>用户与角色权限管理。显示名、角色和启停分别提交；所有授权及自身 / 最后管理员保护由服务端判定。</p>
    {!canManage && <p>当前仅浏览；维护需要 admin 权限，以服务端判定为准。</p>}
    {command.notice && <p role="status">{command.notice}</p>}{command.pending && <p role="status">提交中，请勿重复操作…</p>}{command.cooldown && <p role="status">服务端冷却中，请等待指定时间后手动重试，不会自动请求。</p>}
    {!isOpen && recovery}{!isOpen && unresolved && <Button onPress={() => setOpen(true)}>返回未确认的操作</Button>}
    <Tabs defaultSelectedKey="users"><Tabs.ListContainer><Tabs.List aria-label="管理页签"><Tabs.Tab id="users">用户<Tabs.Indicator /></Tabs.Tab><Tabs.Tab id="roles">角色权限<Tabs.Indicator /></Tabs.Tab></Tabs.List></Tabs.ListContainer>
      <Tabs.Panel id="users" className="space-y-4"><div className="flex flex-wrap items-end gap-3">
        <TextField value={keyword} onChange={setKeyword}><Label>搜索用户名 / 显示名</Label><Input /></TextField>
        <Select value={status || 'all'} onChange={(key) => setStatus(key === 'all' ? '' : String(key))}><Label>用户状态</Label><Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger><Select.Popover><ListBox><ListBox.Item id="all">全部状态</ListBox.Item><ListBox.Item id="active">启用</ListBox.Item><ListBox.Item id="disabled">停用</ListBox.Item></ListBox></Select.Popover></Select>
        <Button variant="secondary" isDisabled={command.cooldown || people.loading} onPress={() => { void loadPeople(); }}>刷新用户</Button><Button isDisabled={!canManage || blockActions || !catalogReady} onPress={() => open({ kind: 'createUser' })}>新建用户</Button>
      </div>{people.loading && <p role="status">加载用户中…</p>}{people.error && <p role="alert">{people.error}</p>}{people.data && <UsersTable filtered={Boolean(status || keyword.trim())} rows={people.data.filter((p) => (!status || p.status === status) && (!keyword.trim() || `${p.username} ${p.displayName}`.toLowerCase().includes(keyword.trim().toLowerCase())))} canManage={canManage} blocked={blockActions} onAction={(kind, user) => open({ kind, user })} />}</Tabs.Panel>
      <Tabs.Panel id="roles" className="space-y-4"><p>内置角色不可删除；内置 admin 不可编辑；其余内置角色不可改 API 档位。</p><div className="flex flex-wrap gap-3"><Button variant="secondary" isDisabled={command.cooldown || catalog.loading} onPress={() => { void loadCatalog(); }}>刷新角色</Button><Button isDisabled={!canManage || blockActions || !catalogReady} onPress={() => open({ kind: 'createRole' })}>新建角色</Button></div>{catalog.data && <RolesTable catalog={catalog.data} canManage={canManage} blocked={blockActions} onAction={(kind, role) => open({ kind, role })} />}</Tabs.Panel>
    </Tabs>
    {catalog.loading && <p role="status">加载角色目录中…</p>}{catalog.error && <div role="alert"><p>{catalog.error}</p><Button isDisabled={command.cooldown} onPress={() => { void loadCatalog(); }}>重试角色目录</Button></div>}
    <Modal.Backdrop isOpen={isOpen} isDismissable={!command.pending && command.phase !== 'checking'} isKeyboardDismissDisabled={command.pending || command.phase === 'checking'} onOpenChange={(open) => { if (!open) close(); }}><Modal.Container size={dialog?.kind === 'sessions' ? 'full' : 'lg'} scroll="inside"><Modal.Dialog>
      <Modal.CloseTrigger aria-label="关闭用户操作" isDisabled={command.pending || command.phase === 'checking'} /><Modal.Header><Modal.Heading>{title}</Modal.Heading>{dialog?.user && <p>{dialog.user.username} · ID {dialog.user.id}</p>}</Modal.Header><Modal.Body className="space-y-4">
        {recovery}{command.notice && dialog?.kind === 'sessions' && <p role="status">{command.notice}</p>}
        {dialog && formKind && <AccountForm kind={dialog.kind as CommandKind} role={dialog.role} draft={draft} catalog={catalog.data ?? emptyCatalog} pending={command.pending} blocked={command.blocked || !catalogReady && ['createUser', 'roles', 'createRole', 'editRole'].includes(dialog.kind)} errors={command.failure?.fieldErrors ?? {}} retry={command.phase === 'allowed'} onEdit={(next) => { setDraft(next); command.edit(); }} onCancel={close} onSubmit={() => { void submit(); }} />}
        {dialog?.kind === 'detail' && <>{publicUser.loading && <p role="status">加载详情中…</p>}{publicUser.error && <p role="alert">{publicUser.error}</p>}{publicUser.data && <dl className="space-y-2"><dt>显示名</dt><dd>{publicUser.data.displayName}</dd><dt>状态</dt><dd>{publicUser.data.status}</dd><dt>角色</dt><dd>{publicUser.data.roles.join('、') || '—'}</dd><dt>API 档位</dt><dd>{publicUser.data.apiTier}</dd><dt>可访问页面（服务端结果）</dt><dd>{publicUser.data.pages.map((page) => <p key={page}>{page}</p>)}</dd><dt>最后登录</dt><dd>{publicUser.data.lastLoginAt ?? '—'}</dd></dl>}<Button isDisabled={command.cooldown || publicUser.loading} onPress={() => { void loadDialog(dialog); }}>刷新详情</Button></>}
        {dialog?.kind === 'sessions' && <>
          <div className="flex flex-wrap gap-3"><Button isDisabled={!canManage || blockActions || sessionList.loading} onPress={() => setRevoke('all')}>吊销全部会话</Button><Button variant="secondary" isDisabled={command.cooldown || sessionList.loading} onPress={() => { void loadDialog(dialog); }}>刷新会话</Button></div>
          {sessionList.loading && <p role="status">加载会话中…</p>}{sessionList.error && <p role="alert">{sessionList.error}</p>}{sessionList.data && <SessionsTable rows={sessionList.data} blocked={blockActions} canManage={canManage} onRevoke={setRevoke} />}
          {revoke && <section aria-label="吊销会话确认"><p>确认吊销{revoke === 'all' ? '该用户全部会话' : `会话 ${revoke}`}？该会话将失效。</p><div className="flex flex-wrap gap-3"><Button variant="danger" isDisabled={command.blocked} onPress={() => { void submit(); }}>{command.phase === 'allowed' ? '明确再次提交' : '确认操作'}</Button><Button variant="secondary" isDisabled={command.pending || unresolved} onPress={() => setRevoke(undefined)}>取消</Button></div></section>}
        </>}
        {(dialog?.kind === 'status' || dialog?.kind === 'deleteRole') && <><p>{dialog.kind === 'status' ? dialog.user?.status === 'active' ? '确认停用用户？停用将吊销其全部会话。' : '确认启用用户？' : `确认删除角色 ${dialog.role?.code}？仍被用户引用时服务端会拒绝。`}</p><div className="flex flex-wrap gap-3"><Button variant="danger" isDisabled={command.blocked} onPress={() => { void submit(); }}>{command.phase === 'allowed' ? '明确再次提交' : '确认操作'}</Button><Button variant="secondary" isDisabled={command.pending} onPress={close}>取消</Button></div></>}
      </Modal.Body></Modal.Dialog></Modal.Container></Modal.Backdrop>
  </div>;
}
