import { useEffect, useRef, useState } from 'react';
import { Button, Label, Modal, TextArea, TextField } from '@heroui/react';
import { ApiError, requestJson, retryAfterSeconds } from '../../shared/api/client';
import { parseIdentityJson } from '../../shared/api/identity-json';
import { withinRequestLifetime } from '../../shared/api/request-lifetime';
import { decodeDetail, decodeDevices, decodeList, decodeTask, decodeTicket, requireIsolatedUploadOrigin, UploadOriginError, uploadOriginConfigurationHelp, type Config, type Device, type Task } from './contracts';
import { Facts } from './TaskFacts';
import type { VideoCommand } from './useVideoCommand';

interface CapturedUpload { file: File; caption: string; deviceSerial: string; deviceLabel: string; clientRequestId: string }
export default function UploadWorkbench({ root, config, canTaskWrite, command, resume, onResumeAccepted, onChanged }: {
  root: string; config?: Config; canTaskWrite: boolean; command: VideoCommand; resume?: Task; onResumeAccepted: () => void; onChanged: () => void;
}) {
  const [file, setFile] = useState<File>(); const [caption, setCaption] = useState(''); const [device, setDevice] = useState('');
  const selectedDevice = useRef<Device | undefined>(undefined);
  const [devices, setDevices] = useState<Device[]>([]); const [deviceNotice, setDeviceNotice] = useState('');
  const [preview, setPreview] = useState(''); const [confirmationPreview, setConfirmationPreview] = useState('');
  const [resumeDeviceLabel, setResumeDeviceLabel] = useState(''); const [metadata, setMetadata] = useState(''); const [message, setMessage] = useState('');
  const [task, setTask] = useState<Task>(); const [captured, setCaptured] = useState<CapturedUpload>();
  const [confirmation, setConfirmation] = useState<CapturedUpload>(); const [completionTarget, setCompletionTarget] = useState<Task>();
  const [uploading, setUploading] = useState(false); const [progress, setProgress] = useState(0); const [uploaded, setUploaded] = useState(false);
  const [checking, setChecking] = useState(false); const [facts, setFacts] = useState<Task>(); const [abandon, setAbandon] = useState(false);
  const xhr = useRef<XMLHttpRequest | undefined>(undefined); const busy = useRef(false); const mounted = useRef(true); const attemptedPath = useRef(''); const draftRevision = useRef(0);
  const readController = useRef<AbortController | undefined>(undefined); const picker = useRef<HTMLInputElement | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; xhr.current?.abort(); readController.current?.abort(); }; }, []);
  useEffect(() => {
    if (!file) { setPreview(''); setMetadata(''); return; }
    const url = URL.createObjectURL(file); setPreview(url); setMetadata('');
    return () => URL.revokeObjectURL(url);
  }, [file]);
  useEffect(() => {
    if (!confirmation) { setConfirmationPreview(''); return; }
    const url = URL.createObjectURL(confirmation.file); setConfirmationPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [confirmation]);
  useEffect(() => {
    if (!uploading) return;
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', beforeUnload); return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [uploading]);
  const canWrite = task ? canTaskWrite : config?.canWrite === true;
  const locked = command.pending || uploading;
  const valid = Boolean(file && config && file.size > 0 && BigInt(file.size) <= BigInt(config.maxVideoBytes) && caption.trim()
    && BigInt(Array.from(caption).length) <= BigInt(config.maxCaptionCharacters));
  function edit() { draftRevision.current++; command.edited(); setMessage(''); }
  function stopRead() { const active = readController.current; readController.current = undefined; active?.abort(); setChecking(false); }
  function pick(next?: File) {
    if (!next || locked) return;
    if (!config || next.type !== 'video/mp4' || !next.name.toLowerCase().endsWith('.mp4') || next.size === 0 || BigInt(next.size) > BigInt(config.maxVideoBytes)) {
      setMessage('请选择符合服务端上限的非空 MP4 视频。'); if (picker.current) picker.current.value = ''; return;
    }
    if (task && (next.name !== task.filename || String(next.size) !== String(task.sizeBytes))) { setMessage('续传必须选择原文件名和原大小的视频；可先放弃续传再创建其他任务。'); if (picker.current) picker.current.value = ''; return; }
    setFile(next); setUploaded(false); edit();
  }
  function clear() {
    if (busy.current || locked || command.needsCheck || command.cooldown) return;
    stopRead();
    setConfirmation(undefined); setCompletionTarget(undefined);
    draftRevision.current++; setFile(undefined); setCaption(''); setDevice(''); selectedDevice.current = undefined; setResumeDeviceLabel(''); setTask(undefined); setCaptured(undefined); setUploaded(false); setFacts(undefined); setMessage(''); setAbandon(false);
    if (picker.current) picker.current.value = '';
  }
  async function loadDevices() {
    if (checking || command.cooldown) return;
    const controller = new AbortController(); readController.current?.abort(); readController.current = controller; setChecking(true);
    try {
      const result = await withinRequestLifetime(controller, (signal) => requestJson<unknown>(root, '/v2/video-publish/devices', { signal }));
      const rows = decodeDevices(result.data); if (mounted.current && readController.current === controller && !controller.signal.aborted) { setDevices(rows); setDeviceNotice('设备列表已更新；原选择保持不变。'); }
    } catch (error: unknown) { if (mounted.current && readController.current === controller) { command.hold(error); setDeviceNotice('设备列表不可用，仍可使用默认设备；不会替换当前选择。'); } }
    finally { if (mounted.current && readController.current === controller) { readController.current = undefined; setChecking(false); } }
  }
  function requestUpload(retry = false) {
    if (!canWrite || command.blocked || locked) return;
    if (retry && captured) {
      setConfirmation(task && file ? { ...captured, file } : captured); return;
    }
    if (!valid || !file) return;
    setConfirmation({ file, caption, deviceSerial: device, deviceLabel: resumeDeviceLabel || devices.find((row) => row.serial === device)?.serialMasked || selectedDevice.current?.serialMasked || config?.target.deviceSerialMasked || '默认设备', clientRequestId: task?.clientRequestId ?? crypto.randomUUID() });
  }
  async function upload(snapshot: CapturedUpload) {
    if (busy.current || command.blocked || !canWrite) return;
    stopRead();
    busy.current = true; setConfirmation(undefined); setCaptured(snapshot); setUploaded(false); setMessage('');
    const expected = task;
    try {
      const validate = (value: unknown) => {
        const candidate = decodeTask(value);
        if (candidate.clientRequestId !== snapshot.clientRequestId || candidate.filename !== snapshot.file.name || String(candidate.sizeBytes) !== String(snapshot.file.size)
          || (expected && candidate.taskId !== expected.taskId)) throw new Error('上传任务身份不匹配');
        try { return decodeTicket(value); }
        catch (error: unknown) {
          // Preserve only the validated captured task, never the rejected signed URL.
          if (mounted.current && error instanceof UploadOriginError) { setTask(candidate); setMessage(uploadOriginConfigurationHelp); }
          throw error;
        }
      };
      attemptedPath.current = expected ? `/v2/video-publish/tasks/${expected.taskId}/upload-url` : '/v2/video-publish/tasks';
      const ticket = await command.send(attemptedPath.current, expected ? {} : {
        clientRequestId: snapshot.clientRequestId, filename: snapshot.file.name, contentType: 'video/mp4', sizeBytes: snapshot.file.size, caption: snapshot.caption,
        ...(snapshot.deviceSerial ? { deviceSerial: snapshot.deviceSerial } : {}),
      }, validate);
      if (!ticket || !mounted.current) return;
      setTask(ticket.task); onChanged();
      if (!ticket.upload) { setMessage('服务端已确认原幂等任务进入后续处理；当前草稿保留供核对，不会再次上传。'); return; }
      requireIsolatedUploadOrigin(new URL(ticket.upload.url));
      const request = new XMLHttpRequest(); xhr.current = request; command.binaryPending(true); setUploading(true); setProgress(0);
      // The origin fence plus withCredentials=false isolates cross-origin PUT credentials.
      // Only Content-Type is sent; no ERP auth/CSRF or response-body diagnostics.
      // Retry-After parsing stays with the shared HTTP policy, including numeric/date waits.
      request.open('PUT', ticket.upload.url); request.withCredentials = false; request.setRequestHeader('Content-Type', 'video/mp4');
      request.upload.onprogress = (event) => { if (mounted.current && event.lengthComputable) setProgress(Math.round(event.loaded / event.total * 100)); };
      await new Promise<void>((resolve, reject) => {
        request.onload = () => request.status >= 200 && request.status < 300 ? resolve() : reject(new ApiError('视频上传未完成', request.status, retryAfterSeconds(request.getResponseHeader('Retry-After'))));
        request.onerror = () => reject(new Error('视频上传中断'));
        request.onabort = () => reject(new Error('上传已停止，服务端对象结果未确认'));
        request.send(snapshot.file);
      });
      if (mounted.current) { setUploaded(true); setProgress(100); setMessage('二进制上传已完成。请核对后明确点击「确认上传并入队」；尚未发布。'); }
    } catch (error: unknown) { if (mounted.current) { command.reject(error); setMessage(error instanceof UploadOriginError ? uploadOriginConfigurationHelp : '上传未确认，文件与文案已保留。先查询当前任务，再明确决定重试上传；不会自动入队。'); } }
    finally { busy.current = false; xhr.current = undefined; if (mounted.current) { command.binaryPending(false); setUploading(false); } }
  }
  async function check() {
    if (!captured || checking || command.cooldown) return;
    const revision = draftRevision.current;
    const controller = new AbortController(); readController.current?.abort(); readController.current = controller; setChecking(true);
    try {
      const result = await withinRequestLifetime(controller, (signal) => requestJson<unknown>(root, task ? `/v2/video-publish/tasks/${task.taskId}` : '/v2/video-publish/tasks', { signal }, parseIdentityJson));
      const found = task ? decodeDetail(result.data) : decodeList(result.data).items.find((row) => row.clientRequestId === captured.clientRequestId);
      if (found && (found.clientRequestId !== captured.clientRequestId || (task && found.taskId !== task.taskId))) throw new Error('任务身份不匹配');
      if (!mounted.current || readController.current !== controller || controller.signal.aborted || revision !== draftRevision.current) return;
      setFacts(found); if (found) setTask(found);
      command.checked(attemptedPath.current); setMessage(found ? '已读取当前事实，不代表原提交或对象上传已确认。请自行核对后决定下一步。' : '当前第一页未查到匹配任务，不证明不存在或原创建未提交；原幂等键与请求仍保留。');
    } catch (error: unknown) {
      if (!mounted.current || readController.current !== controller || revision !== draftRevision.current) return;
      command.hold(error);
      if (error instanceof ApiError && error.status === 404) { command.checked(attemptedPath.current); setMessage('当前记录不可见或不存在，不证明原提交未生效。原草稿与幂等键保留。'); }
      else setMessage('当前事实读取未完成，请稍后明确重试查询；草稿保留。');
    } finally { if (mounted.current && readController.current === controller) { readController.current = undefined; setChecking(false); } }
  }
  async function complete() {
    if (!completionTarget || command.blocked || !canWrite) return;
    stopRead();
    const target = completionTarget; setCompletionTarget(undefined);
    attemptedPath.current = `/v2/video-publish/tasks/${target.taskId}/confirm-upload`;
    const result = await command.send(attemptedPath.current, { rowVersion: target.rowVersion }, (value) => {
      const row = decodeTask(value); if (row.taskId !== target.taskId) throw new Error('任务身份不匹配'); return row;
    });
    if (result) { setTask(result); setUploaded(false); setMessage('服务端已确认上传并入队。草稿仍保留；可明确清除后创建下一任务。'); onChanged(); }
  }
  async function acceptResume() {
    if (!resume || locked || checking || command.needsCheck || command.cooldown || !resume.allowedActions.includes('continue_upload')) return;
    const target = resume; const revision = draftRevision.current; const controller = new AbortController();
    readController.current?.abort(); readController.current = controller; setChecking(true);
    try {
      const result = await withinRequestLifetime(controller, (signal) => requestJson<unknown>(root, `/v2/video-publish/tasks/${target.taskId}`, { signal }, parseIdentityJson));
      const row = decodeDetail(result.data);
      if (!mounted.current || readController.current !== controller || controller.signal.aborted) return;
      if (row.taskId !== target.taskId || row.clientRequestId !== target.clientRequestId || !row.allowedActions.includes('continue_upload')) throw new Error('续传事实不匹配');
      if (revision !== draftRevision.current) { setMessage('加载期间草稿已修改，未覆盖输入。请再次明确接受续传。'); return; }
      setTask(row); setCaptured(undefined); setFile(undefined); setCaption(row.caption); setDevice(''); setResumeDeviceLabel(row.target.deviceSerialMasked); setUploaded(false); setFacts(undefined);
      if (picker.current) picker.current.value = '';
      setMessage('已选择续传任务；请选择原视频。原任务身份与完整文案保留。'); onResumeAccepted();
    } catch (error: unknown) { if (mounted.current && readController.current === controller && revision === draftRevision.current) { command.hold(error); setMessage('续传事实读取失败或动作已变化；当前草稿未覆盖，请刷新核对。'); } }
    finally { if (mounted.current && readController.current === controller) { readController.current = undefined; setChecking(false); } }
  }
  return <section aria-label="新建发布任务" className="space-y-3 rounded-lg border border-default p-4">
    <h2 className="text-xl font-semibold">新建发布任务 / 继续上传</h2>
    <p>上传前确认完整文案与设备；二进制完成后须明确确认入队。任务可能立即开始，以服务端允许操作为准。</p>
    {resume && <div className="space-y-2"><p>待续传：{resume.taskId} · {resume.filename}。接受将替换当前本地文件与文案，请先保留需要的草稿。</p><Button isDisabled={locked || checking || command.needsCheck || command.cooldown} onPress={() => { void acceptResume(); }}>确认替换草稿并续传此任务</Button><Button variant="secondary" onPress={onResumeAccepted}>保留当前草稿</Button></div>}
    <label className="block">选择 MP4 视频<input ref={picker} type="file" accept="video/mp4,.mp4" disabled={locked} onChange={(event) => pick(event.target.files?.[0])} /></label>
    {file && <><p className="break-all">{file.name} · {file.size} 字节</p><Button variant="secondary" isDisabled={locked} onPress={() => picker.current?.click()}>重新选择文件</Button></>}
    {preview && <video src={preview} aria-label="本地视频预览" controls muted playsInline className="max-h-64 max-w-full" onLoadedMetadata={(event) => { const video = event.currentTarget; setMetadata(`${Number.isFinite(video.duration) ? Math.round(video.duration) : '未知'} 秒 · ${video.videoWidth}×${video.videoHeight}`); }} />}
    {metadata && <p>{metadata}</p>}
    <TextField value={caption} isDisabled={locked || Boolean(task)} onChange={(value) => { setCaption(value); edit(); }}><Label>发布文案</Label><TextArea rows={5} autoComplete="off" /></TextField>
    {captured && caption !== captured.caption && <p>本地草稿与原请求不同；重试使用确认对话框中展示的原始文案，不覆盖你的草稿。</p>}
    <p>{Array.from(caption).length} / {config?.maxCaptionCharacters ?? '加载中'} 字符 · 文件上限 {config?.maxVideoBytes ?? '加载中'} 字节</p>
    {command.failure?.fieldErrors.caption && <p role="alert">{command.failure.fieldErrors.caption}</p>}
    <label>目标设备<select aria-label="创建目标设备" value={device} disabled={locked || Boolean(task)} onChange={(event) => { setDevice(event.target.value); selectedDevice.current = devices.find((row) => row.serial === event.target.value); edit(); }}><option value="">默认 · {config?.target.deviceSerialMasked ?? '未配置'}</option>{device && !devices.some((row) => row.serial === device) && <option value={device}>{selectedDevice.current?.serialMasked ?? '当前选择'}（本次列表未返回；提交时由服务端校验）</option>}{devices.map((row) => <option key={row.serial} value={row.serial}>{row.serialMasked} · {row.model || row.product} · {row.state}{row.isBusy ? ' · 忙' : ''}{row.isEmulator ? ' · 模拟器' : ''}</option>)}</select></label>
    <Button variant="secondary" isDisabled={checking || command.cooldown} onPress={() => { void loadDevices(); }}>加载设备列表</Button>{deviceNotice && <p role="status">{deviceNotice}</p>}
    {!canWrite && <p>{task ? '继续上传与确认入队需要 readwrite 能力提示，以服务端校验为准。' : config?.writeBlockReason ?? '服务端创建条件尚不可用。'}</p>}
    {task && <Facts items={[[ '上传任务 ID', task.taskId ], ['上传 rowVersion', task.rowVersion], ['当前服务端阶段', task.stageLabel], ['原 clientRequestId', task.clientRequestId]]} />}
    <div className="flex flex-wrap gap-2">
      {!captured && <Button isDisabled={!valid || !canWrite || command.blocked || locked || (Boolean(task) && !task?.allowedActions.includes('continue_upload'))} onPress={() => requestUpload()}>上传并加入发布队列</Button>}
      {captured && <Button isDisabled={!canWrite || command.blocked || locked || (Boolean(task) && !task?.allowedActions.includes('continue_upload'))} onPress={() => requestUpload(true)}>明确重试原上传</Button>}
      {captured && !task && command.failure && !command.failure.uncertain && !command.needsCheck && <Button variant="secondary" isDisabled={locked || command.cooldown} onPress={() => setCaptured(undefined)}>使用当前草稿重新准备</Button>}
      {captured && <Button variant="secondary" isDisabled={checking || command.cooldown || locked} onPress={() => { void check(); }}>核对上传任务</Button>}
      {uploaded && task?.allowedActions.includes('continue_upload') && <Button isDisabled={!canWrite || command.blocked || locked} onPress={() => setCompletionTarget(task)}>确认上传并入队</Button>}
      <Button variant="secondary" isDisabled={locked || command.needsCheck || command.cooldown} onPress={() => setAbandon(true)}>清除选择 / 放弃续传</Button>
      {uploading && <Button variant="danger" onPress={() => xhr.current?.abort()}>取消上传</Button>}
    </div>
    {uploading && <progress aria-label="视频上传进度" max={100} value={progress}>{progress}%</progress>}
    {message && <p role="status">{message}</p>}
    {facts && <Facts items={[[ '核对任务', facts.taskId ], ['核对状态', facts.statusLabel], ['核对阶段', facts.stageLabel], ['核对版本', facts.rowVersion], ['核对允许操作', facts.allowedActions.join('、')]]} />}
    <Modal.Backdrop isOpen={Boolean(confirmation)} onOpenChange={(open) => { if (!open) setConfirmation(undefined); }}><Modal.Container size="lg" scroll="inside"><Modal.Dialog><Modal.Header><Modal.Heading>确认加入发布队列</Modal.Heading></Modal.Header><Modal.Body>
      {confirmationPreview && <video src={confirmationPreview} aria-label="确认视频预览" controls muted playsInline className="max-h-64 max-w-full" />}
      <p>请核对文件、完整文案和目标。上传完成后需明确确认入队；入队后设备空闲时可能立即开始。</p><Facts items={[[ '文件', confirmation?.file.name ], ['大小（字节）', confirmation?.file.size], ['完整文案', confirmation?.caption], ['App', config?.target.appName], ['设备', confirmation?.deviceLabel], ['相册', config?.target.album]]} />
    </Modal.Body><Modal.Footer><Button variant="secondary" onPress={() => setConfirmation(undefined)}>返回修改</Button><Button isDisabled={command.blocked || uploading} onPress={() => { if (confirmation) void upload(confirmation); }}>确认并上传</Button></Modal.Footer></Modal.Dialog></Modal.Container></Modal.Backdrop>
    <Modal.Backdrop isOpen={Boolean(completionTarget)} onOpenChange={(open) => { if (!open) setCompletionTarget(undefined); }}><Modal.Container><Modal.Dialog><Modal.Header><Modal.Heading>确认上传入队</Modal.Heading></Modal.Header><Modal.Body><p>任务 {completionTarget?.taskId} · rowVersion {completionTarget?.rowVersion}。确认后设备可能立即开始发布。</p></Modal.Body><Modal.Footer><Button variant="secondary" onPress={() => setCompletionTarget(undefined)}>返回核对</Button><Button isDisabled={!canWrite || command.blocked} onPress={() => { void complete(); }}>确认入队</Button></Modal.Footer></Modal.Dialog></Modal.Container></Modal.Backdrop>
    <Modal.Backdrop isOpen={abandon} onOpenChange={setAbandon}><Modal.Container><Modal.Dialog><Modal.Header><Modal.Heading>放弃本地草稿？</Modal.Heading></Modal.Header><Modal.Body><p>只清除本地文件与文案，不取消或删除服务端任务。请先保存需要的内容。</p></Modal.Body><Modal.Footer><Button variant="secondary" onPress={() => setAbandon(false)}>保留草稿</Button><Button onPress={clear}>确认清除本地草稿</Button></Modal.Footer></Modal.Dialog></Modal.Container></Modal.Backdrop>
  </section>;
}
