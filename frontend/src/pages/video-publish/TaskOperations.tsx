import { useEffect, useRef, useState } from 'react';
import { Button, Input, Label, Modal, TextArea, TextField } from '@heroui/react';
import { ApiError, requestJson } from '../../shared/api/client';
import { parseIdentityJson } from '../../shared/api/identity-json';
import { withinRequestLifetime } from '../../shared/api/request-lifetime';
import { decodeDetail, decodeTask, type Detail, type Task } from './contracts';
import { Facts } from './TaskFacts';
import type { VideoCommand } from './useVideoCommand';

export const actionLabels: Record<string, string> = { retry: '重试原任务', cancel: '取消任务', verify: '再次自动核验', retry_cleanup: '重试清理', replace_upload: '重新上传', confirm_published: '确认已发布', edit_settings: '编辑发布设置', continue_upload: '重新选择文件继续' };
export interface Operation { task: Task; action: string; root: string }
export function ActionButtons({ task, disabled, onAction }: { task: Task; disabled: boolean; onAction: (task: Task, action: string) => void }) {
  return <div className="flex flex-wrap gap-2">{task.allowedActions.filter((action) => Object.hasOwn(actionLabels, action)).map((action) => <Button key={action} size="sm" variant="secondary" isDisabled={disabled} onPress={() => onAction(task, action)}>{action === 'confirm_published' ? '打开人工确认' : actionLabels[action]}</Button>)}</div>;
}
function evidence(task: Task, action: string): string {
  const session = task.currentAttempt?.artemisSessionId ?? task.latestArtemisSessionId ?? '尚未创建';
  switch (action) {
    case 'retry': return `前次 Artemis ID：${session}。失败阶段：${task.stageLabel}。错误：${task.lastErrorCode ?? '无'} / ${task.lastErrorMessage ?? '无'}。AI 报告失败可能有误，请先自行核验作品页再决定是否重试。将使用原视频和原文案创建新的执行记录。`;
    case 'cancel': return `取消任务将保留审计记录和原视频，不删除对象。当前阶段：${task.stageLabel}。`;
    case 'verify': return `核验关联发布 attempt ${task.relatedPublishAttempt?.artemisSessionId ?? session}；只查看作品页和草稿箱，不进入上传页，不再次点击发布。`;
    case 'retry_cleanup': return `仅重试服务端提供的失败资源：${task.cleanupRetryableResources.join('、') || '无'}；不改变业务发布结果，对象不删除。`;
    case 'replace_upload': return '重新上传将重开此原任务的上传输入。只在服务端明确提供此动作时执行，不创建另一业务任务。';
    default: return '以服务端任务状态与允许操作为准。';
  }
}
export default function TaskOperations({ operation, command, canWrite, onClose, onChanged, onResume }: {
  operation: Operation; command: VideoCommand; canWrite: boolean; onClose: () => void; onChanged: () => void; onResume: (task: Task) => void;
}) {
  const { task: original, action } = operation;
  const [facts, setFacts] = useState<Detail>(); const [dialog, setDialog] = useState(action !== 'confirm_published' && action !== 'edit_settings');
  const [checking, setChecking] = useState(false); const [message, setMessage] = useState('');
  const [prompt, setPrompt] = useState(''); const [device, setDevice] = useState(''); const [appPackage, setAppPackage] = useState('');
  const [discard, setDiscard] = useState(false); const [settled, setSettled] = useState(false);
  const [locked, setLocked] = useState(false); const controller = useRef<AbortController | undefined>(undefined); const mounted = useRef(true);
  const sent = useRef(false); const attemptedPath = useRef(''); const dirty = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; controller.current?.abort(); controller.current = undefined; }; }, []);
  const current = facts ?? original;
  const endpoint = `/v2/video-publish/tasks/${original.taskId}/${action === 'retry_cleanup' ? 'cleanup/retry' : action === 'replace_upload' ? 'replace-upload' : action === 'edit_settings' ? 'settings' : action.replaceAll('_', '-')}`;
  const allowed = canWrite && current.allowedActions.includes(action) && !locked && !settled;
  async function check() {
    if (checking || command.cooldown || command.pending) return;
    const abort = new AbortController(); controller.current?.abort(); controller.current = abort; setChecking(true);
    try {
      const result = await withinRequestLifetime(abort, (signal) => requestJson<unknown>(operation.root, `/v2/video-publish/tasks/${original.taskId}`, { signal }, parseIdentityJson));
      const row = decodeDetail(result.data); if (row.taskId !== original.taskId) throw new Error('任务身份不匹配');
      if (!mounted.current || controller.current !== abort || abort.signal.aborted) return;
      setFacts(row); setSettled(false); command.checked(attemptedPath.current);
      if ((action === 'edit_settings' || action === 'cancel') && row.paramsLockedAt !== null) setLocked(true);
      if (action === 'edit_settings' && !dirty.current) setAppPackage(row.target.appPackage);
      setMessage('当前事实已读取，仅供核对，不证明原操作是否提交；输入保留。请根据当前版本与允许操作明确决定是否再次操作。');
    } catch (error: unknown) {
      if (!mounted.current || controller.current !== abort) return;
      command.hold(error);
      if (error instanceof ApiError && error.status === 404) { command.checked(attemptedPath.current); setMessage('当前任务不可见或不存在，不证明原操作未提交；原目标及输入保留。'); }
      else setMessage('读取当前事实失败；原目标及输入保留，请明确重试查询。');
    } finally { if (mounted.current && controller.current === abort) { controller.current = undefined; setChecking(false); } }
  }
  async function perform() {
    if (sent.current || checking || command.blocked || !allowed || (action === 'edit_settings' && !facts)) return;
    const target = current;
    const body: Record<string, unknown> = { rowVersion: target.rowVersion };
    if (action === 'retry_cleanup') {
      const resources = target.cleanupRetryableResources.filter((name) => name === 'device' || name === 'spool');
      if (!resources.length) { setMessage('没有服务端提供的可重试设备或 spool 资源。'); return; }
      body.resources = resources;
    }
    if (action === 'edit_settings') {
      if (prompt.trim()) body.prompt = prompt;
      if (device.trim()) body.deviceSerial = device.trim();
      if (appPackage.trim() && appPackage.trim() !== facts?.target.appPackage) body.appPackage = appPackage.trim();
      if (Object.keys(body).length === 1) { setMessage('没有需要保存的修改。空白字段表示不修改；设备掩码不会作为序列号提交。'); return; }
    }
    sent.current = true; attemptedPath.current = endpoint; setDialog(false); setMessage('');
    const result = await command.send(endpoint, body, (value) => {
      const row = decodeTask(value); if (row.taskId !== target.taskId) throw new Error('任务身份不匹配'); return row;
    }, action === 'edit_settings' ? 'PATCH' : 'POST');
    sent.current = false;
    if (!mounted.current) return;
    if (result) {
      setSettled(true);
      setMessage('服务端已确认本次操作。请读取当前事实查看最新状态或审计；不会自动再次提交。'); onChanged();
      if (action === 'replace_upload') onResume(result);
    }
  }
  const label = actionLabels[action] ?? action;
  return <section aria-label="任务操作与核对" className="space-y-3 rounded-lg border border-default p-4">
    <h2 className="text-xl font-semibold">{label}</h2><Facts items={[[ '捕获任务 ID', original.taskId ], ['原 rowVersion', original.rowVersion], ['当前核对版本', facts?.rowVersion], ['原文件', original.filename]]} />
    <p className="whitespace-pre-wrap break-all">{evidence(original, action)}</p>
    {action === 'confirm_published' && <p>请先人工确认作品已发布。此按钮是唯一成功确认入口；确认人与时间由服务器记录，确认后不能撤销。</p>}
    <Button variant="secondary" isDisabled={checking || command.cooldown || command.pending} onPress={() => { void check(); }}>查询当前事实</Button>
    {facts && <Facts items={[[ '当前业务状态', facts.statusLabel], ['当前阶段', facts.stageLabel], ['允许操作', facts.allowedActions.join('、')], ['参数锁定', facts.paramsLockedAt], ['人工确认人', facts.confirmation?.confirmedBy], ['人工确认时间', facts.confirmation?.confirmedAt]]} />}
    {action === 'edit_settings' && <div className="space-y-3"><p>先查询当前设置。正文与设备序列号留空表示不修改；不会用掩码或旧 Prompt 猜测当前值。</p>
      <TextField value={prompt} isDisabled={command.pending || locked} onChange={(value) => { setPrompt(value); dirty.current = true; command.edited(); }}><Label>正文 Prompt</Label><TextArea rows={4} autoComplete="off" /></TextField>
      <TextField value={device} isDisabled={command.pending || locked} onChange={(value) => { setDevice(value); dirty.current = true; command.edited(); }}><Label>设置设备序列号</Label><Input autoComplete="off" /></TextField>
      <TextField value={appPackage} isDisabled={command.pending || locked} onChange={(value) => { setAppPackage(value); dirty.current = true; command.edited(); }}><Label>App 包名</Label><Input autoComplete="off" /></TextField>
      {Object.entries(command.failure?.fieldErrors ?? {}).map(([key, error]) => <p key={key} role="alert">{error}</p>)}
      {locked && <p>参数已提交给发布端，不能再修改。</p>}
    </div>}
    <div className="flex flex-wrap gap-2"><Button isDisabled={command.blocked || checking || !allowed || (action === 'edit_settings' && !facts)} onPress={() => {
      if (action === 'confirm_published' || action === 'edit_settings') void perform(); else setDialog(true);
    }}>{action === 'edit_settings' ? '保存发布设置' : label}</Button><Button variant="secondary" isDisabled={command.pending || command.needsCheck} onPress={() => { if (action === 'edit_settings' && dirty.current) setDiscard(true); else onClose(); }}>关闭操作面板（保留服务端任务）</Button></div>
    {message && <p role="status">{message}</p>}
    <Modal.Backdrop isOpen={dialog} onOpenChange={setDialog}><Modal.Container size="lg" scroll="inside"><Modal.Dialog><Modal.Header><Modal.Heading>{label}确认</Modal.Heading></Modal.Header><Modal.Body><p className="whitespace-pre-wrap break-all">{evidence(current, action)}</p><Facts items={[[ '任务 ID', current.taskId], ['rowVersion', current.rowVersion], ['文件', current.filename]]} /></Modal.Body><Modal.Footer><Button variant="secondary" onPress={() => setDialog(false)}>返回</Button><Button isDisabled={command.blocked || checking || !allowed} onPress={() => { void perform(); }}>{action === 'verify' ? '开始核验' : `确认${label}`}</Button></Modal.Footer></Modal.Dialog></Modal.Container></Modal.Backdrop>
    <Modal.Backdrop isOpen={discard} onOpenChange={setDiscard}><Modal.Container><Modal.Dialog><Modal.Header><Modal.Heading>放弃本地设置草稿？</Modal.Heading></Modal.Header><Modal.Body><p>关闭将清除当前输入，不会修改服务端任务。</p></Modal.Body><Modal.Footer><Button variant="secondary" onPress={() => setDiscard(false)}>继续编辑</Button><Button onPress={onClose}>确认放弃本地设置草稿</Button></Modal.Footer></Modal.Dialog></Modal.Container></Modal.Backdrop>
  </section>;
}
