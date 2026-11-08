import { useEffect, useId, useRef, useState, type ChangeEvent } from 'react';
import { Button, Input, Label, Modal, ProgressBar, TextField } from '@heroui/react';
import { ApiError } from '../../shared/api/client';
import type { FrontendBootstrap } from '../../shared/contracts';
import { decodeDigitalHuman, type UploadTicket } from './contracts';
import { UploadTags } from './TagEditor';
import { ActionError, DiscardDialog, mediaError, mediaWrite, recordLink, uncertainResult, useLeaveGuard } from './media-ui';
import { confirmUpload, imageTypes, requestUpload, transferMediaFile } from './media-upload';

const acceptedTypes = (imageTypes + ',video/mp4,video/webm,video/quicktime').split(',');
type Phase = 'draft' | 'preparing' | 'sending' | 'confirming' | 'attaching' | 'prepare-error' | 'send-error' | 'confirm-error' | 'restart' | 'attach-error';

function LocalFilePreview({ file }: { file: File }) {
  const [preview, setPreview] = useState<{ file: File; url: string | null; failed: boolean }>();
  useEffect(() => {
    let url: string;
    try { url = URL.createObjectURL(file); }
    catch { setPreview({ file, url: null, failed: true }); return; }
    setPreview({ file, url, failed: false });
    return () => URL.revokeObjectURL(url);
  }, [file]);
  const current = preview?.file === file ? preview : undefined;
  function failed() { setPreview((previous) => previous?.file === file ? { ...previous, failed: true } : previous); }
  return <section aria-label="上传前本地预览">
    {!current ? <p role="status">正在准备本地预览…</p> : current.failed || !current.url
      ? <p role="status">暂时无法预览所选文件。你可以继续上传，或重新选择文件。</p>
      : <div className="media-preview media-preview-upload">
          {file.type.startsWith('video/')
            ? <video src={current.url} aria-label={'本地视频预览 ' + file.name} controls preload="metadata" playsInline onError={failed} />
            : <img src={current.url} alt={'本地图片预览 ' + file.name} onError={failed} />}
        </div>}
  </section>;
}

export default function UploadAsset({ bootstrap, target, onClose, onComplete }: {
  bootstrap: FrontendBootstrap;
  target?: { id: string; name: string };
  onClose: (completedAssetId?: string) => void;
  onComplete: (assetId: string, filename: string) => void;
}) {
  const formId = useId();
  const [file, setFile] = useState<File>();
  const fileInput = useRef<HTMLInputElement>(null);
  const [customFilename, setCustomFilename] = useState<string>();
  const filename = customFilename ?? file?.name.slice(0, 255) ?? '';
  const [tags, setTags] = useState<string[]>([]);
  const [ticket, setTicket] = useState<UploadTicket>();
  const [phase, setPhase] = useState<Phase>('draft');
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const [prepareUncertain, setPrepareUncertain] = useState(false);
  const [discard, setDiscard] = useState(false);
  const busy = useRef(false);
  const putUncertain = useRef(false);
  const transfer = useRef<AbortController | undefined>(undefined);
  const pending = phase === 'preparing' || phase === 'sending' || phase === 'confirming' || phase === 'attaching';
  const readyAsset = phase === 'attach-error' ? ticket?.assetId : undefined;
  const href = recordLink(bootstrap, 'media-assets', 'asset', readyAsset);
  const root = bootstrap.rootPath;

  useLeaveGuard(Boolean(file || filename.trim() || tags.length) || pending);
  useEffect(() => () => transfer.current?.abort(), []);

  function close() {
    if (busy.current) return;
    if (file || filename.trim() || tags.length) setDiscard(true);
    else onClose();
  }

  function selectFile(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const next = input.files?.[0];
    if (!next) return;
    setTicket(undefined);
    setPrepareUncertain(false);
    setPhase('draft');
    if (!next.size || !acceptedTypes.includes(next.type)) {
      input.value = '';
      setFile(undefined);
      setError(next.size
        ? '不支持此文件格式。请选择 JPEG、PNG、WebP、GIF、MP4、WebM 或 MOV 文件。'
        : '请选择非空的文件。');
      return;
    }
    setFile(next);
    setCustomFilename((current) => current?.trim() ? current : undefined);
    setError('');
  }

  async function attach(current: UploadTicket) {
    if (target) {
      setPhase('attaching');
      try {
        const linked = await mediaWrite(root, '/digital-humans/' + target.id + '/assets', 'POST', decodeDigitalHuman, { assetId: current.assetId });
        if (linked.digitalHumanId !== target.id || !linked.assetIds.includes(current.assetId)) throw new Error('关联结果不符');
      } catch (failure) {
        setError(mediaError(failure, true));
        setPhase('attach-error');
        return;
      }
    }
    onComplete(current.assetId, filename);
  }

  async function confirm(current: UploadTicket) {
    setPhase('confirming');
    try {
      await confirmUpload(root, current);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 409 && (failure.detail === 'UPLOAD_NOT_FOUND' || failure.detail === 'SIZE_MISMATCH')) {
        const canRetryTransfer = putUncertain.current && Date.parse(current.uploadExpiresAt) > Date.now();
        setError(failure.detail === 'UPLOAD_NOT_FOUND' ? '对象存储中尚未找到此文件。' : '文件大小校验未通过，请重新传输。');
        setPhase(canRetryTransfer ? 'send-error' : 'restart');
      } else {
        setError(mediaError(failure, true));
        setPhase('confirm-error');
      }
      return;
    }
    putUncertain.current = false;
    await attach(current);
  }

  async function send(current: UploadTicket, source: File) {
    setPhase('sending');
    setProgress(0);
    try {
      const controller = new AbortController();
      transfer.current = controller;
      await transferMediaFile(current, source, controller, setProgress);
    } catch (failure) {
      putUncertain.current = true;
      setError(mediaError(failure, true));
      setPhase('send-error');
      return;
    } finally {
      transfer.current = undefined;
    }
    putUncertain.current = false;
    await confirm(current);
  }

  async function start() {
    if (!file || !filename.trim()) return;
    setPhase('preparing');
    let next: UploadTicket;
    try {
      next = await requestUpload(root, file, filename.trim(), tags);
    } catch (failure) {
      setPrepareUncertain(uncertainResult(failure));
      setError(mediaError(failure, true));
      setPhase('prepare-error');
      return;
    }
    setTicket(next);
    await send(next, file);
  }

  async function run(operation: () => Promise<void>) {
    if (busy.current) return;
    busy.current = true;
    setError('');
    try { await operation(); }
    finally { busy.current = false; }
  }

  const editable = phase === 'draft' || phase === 'prepare-error' || phase === 'restart';
  return <>
    <Modal.Backdrop isOpen isDismissable={false} isKeyboardDismissDisabled={pending || discard}
      onOpenChange={(open) => { if (!open) close(); }}>
      <Modal.Container size="lg" scroll="inside">
        <Modal.Dialog>
          <Modal.Header>
            <Modal.Heading>{target ? '上传新素材' : '上传素材'}</Modal.Heading>
            <p className="text-sm text-muted [overflow-wrap:anywhere]">{target ? '上传后添加到「' + target.name + '」。' : '上传后添加到全局素材库。'}</p>
          </Modal.Header>
          <Modal.Body>
            <form id={formId} className="media-form" onSubmit={(event) => {
              event.preventDefault();
              if (editable && file?.size) void run(start);
            }}>
              <TextField value={filename} onChange={setCustomFilename} isDisabled={!editable}>
                <Label>素材名称</Label><Input maxLength={255} autoFocus />
              </TextField>
              <div className="media-upload-file">
                <span className={file ? undefined : 'text-muted'}>{file?.name ?? '图片或视频'}</span>
                <input ref={fileInput} type="file" hidden aria-label="素材文件" accept={acceptedTypes.join(',')}
                  disabled={!editable} onChange={selectFile} />
                <Button type="button" variant="secondary" isDisabled={!editable} onPress={() => fileInput.current?.click()}>
                  {file ? '更换文件' : '上传文件'}
                </Button>
              </div>
              {file && <LocalFilePreview file={file} />}
              <UploadTags root={root} selected={tags} onChange={setTags} disabled={!editable} />
              {phase === 'preparing' && <p role="status">正在准备上传…</p>}
              {phase === 'sending' && <>
                <p role="status">{progress === 100 ? '文件已发送，等待存储响应…' : '正在传输文件'}</p>
                <ProgressBar aria-label="传输进度" maxValue={100} value={progress}>
                  <ProgressBar.Output /><ProgressBar.Track><ProgressBar.Fill /></ProgressBar.Track>
                </ProgressBar>
                <Button variant="secondary" onPress={() => transfer.current?.abort()}>中止传输</Button>
              </>}
              {phase === 'confirming' && <>
                <p role="status">传输完成，正在确认入库</p>
                <ProgressBar aria-label="传输完成" maxValue={100} value={100}>
                  <ProgressBar.Output /><ProgressBar.Track><ProgressBar.Fill /></ProgressBar.Track>
                </ProgressBar>
              </>}
              {phase === 'attaching' && <p role="status" className="[overflow-wrap:anywhere]">资产已入库，正在添加到 {target?.name}…</p>}
              {phase === 'send-error' && <p>文件传输结果尚未确认。先检查原记录是否可以入库，也可重试同一个上传。</p>}
              {phase === 'confirm-error' && <p>已发送文件，入库状态待核对。重试确认会继续处理原记录。</p>}
              {phase === 'attach-error' && <p role="status" className="[overflow-wrap:anywhere]">资产「{filename}」已入库，但尚未确认添加到「{target?.name}」。重试只会添加这项资产。</p>}
              <ActionError message={error} />
              {phase === 'prepare-error' && prepareUncertain && <p>上传准备结果未确认。再次开始前请在资产库核对同名记录。{href && <> <a className="underline" href={href} target="_blank" rel="noreferrer">在新标签页核对资产库</a></>}</p>}
            </form>
          </Modal.Body>
          <Modal.Footer className="flex-wrap">
            {!pending && <Button variant="secondary" onPress={close}>{readyAsset ? '关闭' : '取消'}</Button>}
            {editable && <Button type="submit" form={formId} isDisabled={!file?.size || !filename.trim()}>
              {phase === 'restart' ? '重新上传文件' : '开始上传'}
            </Button>}
            {(phase === 'send-error' || phase === 'confirm-error') && ticket && <Button onPress={() => void run(() => confirm(ticket))}>
              {phase === 'send-error' ? '检查并确认入库' : '重试入库'}
            </Button>}
            {phase === 'send-error' && ticket && file && <Button variant="secondary" onPress={() => {
              if (Date.parse(ticket.uploadExpiresAt) <= Date.now()) {
                setError('原上传地址已过期，请重新上传文件。');
                setPhase('restart');
                return;
              }
              void run(() => send(ticket, file));
            }}>重试原文件传输</Button>}
            {phase === 'attach-error' && ticket && <Button onPress={() => void run(() => attach(ticket))}>重试添加</Button>}
            {readyAsset && href && <a className="self-center underline" href={href} target="_blank" rel="noreferrer">在新标签页查看已入库资产</a>}
          </Modal.Footer>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
    <DiscardDialog open={discard} onKeep={() => setDiscard(false)} onDiscard={() => onClose(readyAsset)}
      description={readyAsset ? '已入库的资产会保留，你可以稍后通过「选择已有资产」添加。'
        : ticket ? '结束后会丢弃本次上传的本地进度。请在资产库核对已完成的记录。' : '所选文件和填写的名称将清除。'} />
  </>;
}
