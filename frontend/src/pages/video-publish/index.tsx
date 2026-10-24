import { useEffect, useState } from 'react';
import { Alert, Button, Modal, Table } from '@heroui/react';
import type { PageProps } from '../../shared/contracts';
import { decodeConfig, decodeCurrent, decodeDetail, decodeList, type Task } from './contracts';
import { AttemptFacts, AvailableActions, DetailFacts, Facts, Identity, TargetFacts } from './TaskFacts';
import { useVideoRead } from './useVideoRead';
import { useVideoCommand } from './useVideoCommand';
import UploadWorkbench from './UploadWorkbench';
import TaskOperations, { ActionButtons, type Operation } from './TaskOperations';

const api = '/v2/video-publish';
const refreshKey = 'tts-erp.video-publish.refresh-preference';
const filters = [['', '全部'], ['pending', '排队'], ['running', '执行中'], ['failed', '失败'], ['needs_review', '需核验'], ['succeeded', '成功'], ['cancelled', '已取消']] as const;
function ReadError({ name, error }: { name: string; error?: string }) {
  return error && <Alert status="danger"><Alert.Content><Alert.Title>{name}不可用</Alert.Title><Alert.Description>{error}</Alert.Description></Alert.Content></Alert>;
}
function PollFacts({ poll }: { poll: { running: boolean; cleaning: boolean; queued: boolean } }) {
  return <p>服务端活动标记：执行 {String(poll.running)} · 清理 {String(poll.cleaning)} · 排队 {String(poll.queued)}</p>;
}
function unique(tasks: Task[]): Task[] { const seen = new Set<string>(); return tasks.filter((task) => { if (seen.has(task.taskId)) return false; seen.add(task.taskId); return true; }); }

function VideoPublishContent({ bootstrap }: PageProps) {
  const [revision, setRevision] = useState(0);
  const command = useVideoCommand(bootstrap.rootPath);
  const canTaskWrite = bootstrap.roleCapabilities?.readwrite === true;
  const [resume, setResume] = useState<Task>();
  const [operation, setOperation] = useState<Operation>();
  function actOn(task: Task, action: string) {
    if (command.blocked || !canTaskWrite) return;
    if (operation) { setNotice('请先完成或关闭当前操作面板；原目标和输入不会被新选择覆盖。'); return; }
    if (action === 'continue_upload') { setResume(task); setSelected(null); return; }
    setSelected(null); setOperation({ task, action, root: bootstrap.rootPath });
  }
  const [auto, setAuto] = useState(() => { try { return localStorage.getItem(refreshKey) !== 'off'; } catch { return true; } });
  const [visible, setVisible] = useState(() => !document.hidden);
  const [filter, setFilter] = useState('');
  const [selection, setSelection] = useState<{ root: string; id: string } | null>(null);
  const selected = selection?.root === bootstrap.rootPath ? selection.id : null;
  const [appendQuery, setAppendQuery] = useState<{ root: string; path: string } | null>(null);
  const appendPath = appendQuery?.root === bootstrap.rootPath ? appendQuery.path : null;
  function setSelected(id: string | null) { setSelection(id === null ? null : { root: bootstrap.rootPath, id }); }
  function setAppendPath(path: string | null) { setAppendQuery(path === null ? null : { root: bootstrap.rootPath, path }); }
  const [extra, setExtra] = useState<{ filter: string; items: Task[]; nextCursor: string | null; appended: boolean }>({ filter: '', items: [], nextCursor: null, appended: false });
  const [notice, setNotice] = useState('');
  useEffect(() => { const update = () => setVisible(!document.hidden); document.addEventListener('visibilitychange', update); return () => document.removeEventListener('visibilitychange', update); }, []);
  const params = new URLSearchParams(); if (filter) params.set('status', filter);
  const listPath = `${api}/tasks${params.size ? `?${params}` : ''}`;
  const config = useVideoRead(bootstrap.rootPath, `${api}/config`, decodeConfig, revision, auto, visible);
  const current = useVideoRead(bootstrap.rootPath, `${api}/tasks/current`, decodeCurrent, revision, auto, visible);
  const history = useVideoRead(bootstrap.rootPath, listPath, decodeList, revision, auto, visible);
  const more = useVideoRead(bootstrap.rootPath, appendPath, decodeList, revision, false, visible);
  const detail = useVideoRead(bootstrap.rootPath, selected ? `${api}/tasks/${selected}` : null, decodeDetail, revision, auto, visible);
  useEffect(() => {
    if (!more.data || !appendPath) return;
    const incoming = more.data;
    setExtra((old) => ({ filter, items: unique([...(old.filter === filter ? old.items : []), ...incoming.items]), nextCursor: incoming.nextCursor, appended: true }));
    setAppendPath(null);
  }, [more.data, appendPath, filter]);
  // A root change must not carry any previous deployment's appended records or selection.
  useEffect(() => { setExtra({ filter: '', items: [], nextCursor: null, appended: false }); setAppendPath(null); setSelected(null); setResume(undefined); setOperation(undefined); }, [bootstrap.rootPath]);
  const rows = history.data ? unique([...history.data.items, ...(extra.filter === filter ? extra.items : [])]) : [];
  const nextCursor = extra.filter === filter && extra.appended ? extra.nextCursor : history.data?.nextCursor;
  const blocked = config.blocked || current.blocked || history.blocked || more.blocked || detail.blocked;
  async function copy(value: string) {
    try { await navigator.clipboard.writeText(value); setNotice('ID 已复制（完整值）。'); }
    catch { setNotice('复制失败，请选择完整 ID 手动复制。'); }
  }
  function chooseFilter(value: string) { setFilter(value); setAppendPath(null); setExtra({ filter: value, items: [], nextCursor: null, appended: false }); }
  function loadMore() {
    if (!nextCursor || more.loading || blocked) return;
    const query = new URLSearchParams(); if (filter) query.set('status', filter); query.set('cursor', nextCursor);
    setAppendPath(`${api}/tasks?${query}`);
  }
  function toggleAuto(value: string) { const enabled = value === 'on'; setAuto(enabled); try { localStorage.setItem(refreshKey, enabled ? 'on' : 'off'); } catch { /* Preference persistence is optional; no task data is stored. */ } }
  return <div className="min-w-0 space-y-6">
    {command.failure && <Alert status="danger"><Alert.Content><Alert.Title>操作未完成</Alert.Title><Alert.Description>{command.failure.message}</Alert.Description></Alert.Content></Alert>}
    {command.cooldown && <p role="status">请等待完整 Retry-After；读取、编辑或刷新不会缩短写入冷却，期满也不会自动重试。</p>}
    {command.needsCheck && <p role="status">原操作结果待核对。先使用对应操作的「查询当前事实」或「核对上传任务」，再由你明确决定是否重试。</p>}
    <UploadWorkbench key={bootstrap.rootPath} root={bootstrap.rootPath} config={config.data} canTaskWrite={canTaskWrite} command={command} resume={resume} onResumeAccepted={() => setResume(undefined)} onChanged={() => setRevision((value) => value + 1)} />
    {operation?.root === bootstrap.rootPath && <TaskOperations operation={operation} command={command} canWrite={canTaskWrite} onClose={() => setOperation(undefined)} onChanged={() => setRevision((value) => value + 1)} onResume={(task) => { setResume(task); }} />}
    <div className="flex flex-wrap items-center gap-3"><label className="flex flex-wrap items-center gap-2">自动刷新<select aria-label="自动刷新" value={auto ? 'on' : 'off'} onChange={(event) => toggleAuto(event.target.value)}><option value="on">开（每 5 秒）</option><option value="off">关</option></select></label>
      <Button variant="secondary" isDisabled={blocked} onPress={() => setRevision((value) => value + 1)}>立即刷新</Button>
      <span>上一响应结束后计时；隐藏页面暂停。</span>
    </div>
    {blocked && <p role="status">读取冷却中；等待完整 Retry-After 后可明确刷新，不会自动重试。</p>}
    {notice && <p role="status">{notice}</p>}
    {!canTaskWrite && <p>任务写操作需要服务端提供的 readwrite 能力提示；仍可读取详情，接口会再次验证权限。创建条件与任务允许操作分别由服务端判定。</p>}
    <section aria-label="发布配置" className="space-y-3"><h2 className="text-xl font-semibold">发布配置与服务端能力</h2>
      {config.loading && !config.data && <p role="status">加载配置中…</p>}<ReadError name="配置" error={config.error} />
      {config.data && <><TargetFacts target={config.data.target} /><Facts items={[
        ['允许内容类型', config.data.acceptedContentTypes.join('、')], ['允许扩展名', config.data.acceptedExtensions.join('、')], ['视频上限（字节）', config.data.maxVideoBytes], ['文案上限（字符）', config.data.maxCaptionCharacters], ['上传票据 TTL（秒）', config.data.uploadUrlTtlSeconds],
        ['Worker 状态', config.data.worker.status], ['最近心跳', config.data.worker.lastHeartbeatAt], ['设备状态', config.data.device.status], ['设备说明', config.data.device.message],
        ['Artemis profile', config.data.artemis.profile], ['核验等级', config.data.artemis.verificationLevel], ['Worker poll（秒）', config.data.workerTiming.pollSeconds], ['Worker lease（秒）', config.data.workerTiming.leaseSeconds], ['Worker heartbeat（秒）', config.data.workerTiming.heartbeatSeconds],
        ['创建条件 canWrite', config.data.canWrite], ['创建阻止原因', config.data.writeBlockReason], ['配置服务器时间', config.data.serverTime],
      ]} /><p>设备与 Worker 状态、canWrite、队列位置、允许操作均由服务端决定；不由浏览器推算。</p></>}
    </section>
    <section aria-label="当前发布轨道" className="space-y-3"><h2 className="text-xl font-semibold">当前发布轨道</h2>
      {current.loading && !current.data && <p role="status">加载当前任务中…</p>}<ReadError name="当前任务" error={current.error} />
      {current.data && <><PollFacts poll={current.data.pollState} /><p>当前任务服务器时间：{current.data.serverTime} · 上次成功核对：{current.checkedAt}</p>
        {!current.data.tasks.length && <p>当前没有运行中或清理中的发布任务。</p>}
        {current.data.tasks.map((task) => <article key={task.taskId} aria-label={`设备轨道 ${task.deviceSerialMasked ?? task.taskId}`} className="space-y-3 rounded-lg border border-default p-3">
          <h3 className="font-semibold">{task.filename}</h3><Facts items={[
            ['设备', task.deviceSerialMasked], ['任务', task.taskId], ['状态', task.statusLabel], ['阶段', task.stageLabel], ['运行阶段', task.operationalStage], ['运行阶段开始', task.operationalStageStartedAt], ['下次清理重试', task.cleanupNextAttemptAt], ['版本', task.rowVersion],
          ]} /><ol className="list-inside list-decimal"><li>创建 {task.createdAt}</li><li>开始 {task.startedAt ?? '—'}</li><li aria-current="step">{task.stageLabel} · {task.operationalStage}</li></ol>
          <Identity value={task.latestArtemisSessionId} label={`轨道 ${task.taskId} Artemis ID`} copy={(value) => { void copy(value); }} />
          {task.currentAttempt ? <AttemptFacts attempt={task.currentAttempt} label={`轨道 ${task.taskId} 当前执行`} copy={(value) => { void copy(value); }} /> : <p>尚未创建执行记录。</p>}
          <AvailableActions task={task} /><ActionButtons task={task} disabled={command.blocked || !canTaskWrite} onAction={actOn} />{task.allowedActions.includes('view') && <Button variant="secondary" aria-label={`轨道详情 ${task.taskId}`} onPress={() => setSelected(task.taskId)}>查看详情</Button>}
        </article>)}
      </>}
    </section>
    <section aria-label="任务记录" className="min-w-0 space-y-3"><h2 className="text-xl font-semibold">队列与任务记录</h2>
      <label>任务状态 <select aria-label="任务状态" value={filter} onChange={(event) => chooseFilter(event.target.value)}>{filters.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      {history.loading && !history.data && <p role="status">加载任务记录中…</p>}<ReadError name="任务记录" error={history.error} />
      {history.data && <><PollFacts poll={history.data.pollState} /><p>本页返回条数（非业务总数）：{history.data.totalApprox} · 服务器时间：{history.data.serverTime} · 上次成功核对：{history.checkedAt}</p>
        <p>表格区域可横向滚动，保留全部业务列；顺序沿用服务端。</p>
        <Table><Table.ScrollContainer role="region" aria-label="任务表格滚动区域"><Table.Content aria-label="视频发布任务" className="min-w-[95rem]"><Table.Header>
          {['任务 ID / 版本', '状态 / 阶段 / 队列', '视频 / 大小', '文案', '发布 / 核验 / 总执行', 'Artemis ID', '创建者 / 时间', '服务端操作'].map((label, index) => <Table.Column key={label} isRowHeader={index === 0}>{label}</Table.Column>)}
        </Table.Header><Table.Body renderEmptyState={() => '当前筛选下没有任务。'}>{rows.map((task) => <Table.Row key={task.taskId} id={task.taskId}>
          <Table.Cell><code className="block max-w-56 break-all">{task.taskId}</code><p>rowVersion {task.rowVersion}</p></Table.Cell>
          <Table.Cell><p>{task.statusLabel}</p><p>{task.stageLabel} · {task.operationalStage}</p><p>队列位置 {task.queuePosition ?? '—'}</p><p>入队 {task.queuedAt ?? '—'}</p></Table.Cell>
          <Table.Cell><span className="block max-w-56 break-all">{task.filename}</span><p>{task.sizeBytes} 字节</p></Table.Cell><Table.Cell><span className="block max-w-64 whitespace-pre-wrap break-all">{task.captionPreview}</span></Table.Cell>
          <Table.Cell>{task.publishAttemptCount} / {task.verifyAttemptCount} / {task.attemptCount}</Table.Cell>
          <Table.Cell focusMode="child"><Identity value={task.latestArtemisSessionId} label={`任务 ${task.taskId} Artemis ID`} copy={(value) => { void copy(value); }} /></Table.Cell>
          <Table.Cell><p>{task.createdBy}</p><p>{task.createdAt}</p><p>更新 {task.updatedAt}</p></Table.Cell>
          <Table.Cell focusMode="child"><div className="space-y-2"><AvailableActions task={task} /><ActionButtons task={task} disabled={command.blocked || !canTaskWrite} onAction={actOn} />{task.allowedActions.includes('view') && <Button size="sm" variant="secondary" aria-label={`查看详情 ${task.taskId}`} onPress={() => setSelected(task.taskId)}>查看详情</Button>}</div></Table.Cell>
        </Table.Row>)}</Table.Body></Table.Content></Table.ScrollContainer></Table>
        {nextCursor && <Button variant="secondary" isDisabled={more.loading || blocked} onPress={loadMore}>加载更多</Button>}
      </>}
      {more.loading && <p role="status">加载更多任务中…</p>}<ReadError name="后续任务" error={more.error} />
    </section>
    <Modal.Backdrop isOpen={selected !== null} onOpenChange={(open) => { if (!open) setSelected(null); }}><Modal.Container size="lg" scroll="inside"><Modal.Dialog>
      <Modal.CloseTrigger aria-label="关闭任务详情" /><Modal.Header><Modal.Heading>任务详情</Modal.Heading></Modal.Header><Modal.Body className="space-y-4">
        {detail.loading && !detail.data && <p role="status">加载任务详情中…</p>}<ReadError name="任务详情" error={detail.error} />
        <Button variant="secondary" isDisabled={detail.blocked} onPress={() => setRevision((value) => value + 1)}>刷新当前事实</Button>
        {detail.data && detail.data.taskId !== selected && <p role="alert">任务身份不匹配，请关闭后重试。</p>}
        {detail.data && detail.data.taskId === selected && <><DetailFacts task={detail.data} copy={(value) => { void copy(value); }} /><ActionButtons task={detail.data} disabled={command.blocked || !canTaskWrite} onAction={actOn} /></>}
      </Modal.Body></Modal.Dialog></Modal.Container></Modal.Backdrop>
  </div>;
}

export default function VideoPublish(props: PageProps) {
  return <VideoPublishContent key={props.bootstrap.rootPath} {...props} />;
}
