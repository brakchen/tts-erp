import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Button, Input, Label, Modal, TextArea, TextField } from '@heroui/react';
import { ApiError } from '../../shared/api/client';
import { decodeDigitalHuman, type DigitalHuman, type UploadTicket } from '../media-assets/contracts';
import { confirmUpload, imageTypes, requestUpload, transferMediaFile } from '../media-assets/media-upload';
import { ActionError, DiscardDialog, mediaWrite, useLeaveGuard, useMediaAction } from '../media-assets/media-ui';

export type EditorMode = 'create' | 'edit' | 'portrait';

function Camera() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
    <path d="M3 7h4l2-3h6l2 3h4v13H3z" strokeLinejoin="round" /><circle cx="12" cy="13" r="3" />
  </svg>;
}

export function PortraitTile({ url, onPress, label }: {
  url?: string | null; onPress?: () => void; label?: string;
}) {
  const [failed, setFailed] = useState<string>();
  const image = url && failed !== url
    ? <img src={url} alt="" loading="lazy" onError={() => setFailed(url)} />
    : <Camera />;
  return onPress ? <Button isIconOnly variant="ghost" className="media-portrait" aria-label={label}
    aria-haspopup="dialog" onPress={onPress}>
    {image}<span className="media-portrait-icon"><Camera /></span>
  </Button> : <span className="media-portrait" aria-hidden="true">{image}</span>;
}

export default function HumanEditor({ root, mode, human, onClose, onSaved }: {
  root: string; mode: EditorMode; human?: DigitalHuman;
  onClose: () => void; onSaved: (human: DigitalHuman) => void;
}) {
  const [name, setName] = useState(human?.name ?? '');
  const [description, setDescription] = useState(human?.description ?? '');
  const [file, setFile] = useState<File>();
  const [preview, setPreview] = useState<{ file: File; url: string; loaded: boolean }>();
  const [photoError, setPhotoError] = useState('');
  const [discard, setDiscard] = useState(false);
  const [stage, setStage] = useState('');
  const upload = useRef<{ ticket: UploadTicket; sent: boolean; ready: boolean } | undefined>(undefined);
  const transfer = useRef<AbortController | undefined>(undefined);
  const chooser = useRef<HTMLInputElement>(null);
  const writeStarted = useRef(false);
  const action = useMediaAction();
  const dirty = name !== (human?.name ?? '') || description !== (human?.description ?? '') || Boolean(file);
  const photoReady = !file || (preview?.file === file && preview.loaded);
  const canSave = dirty && photoReady && (mode === 'portrait' ? Boolean(file) : Boolean(name.trim()));
  const title = mode === 'create' ? '新建数字人' : mode === 'edit' ? '编辑档案'
    : human?.portraitUrl ? '更换形象照片' : '上传形象照片';
  useLeaveGuard(dirty || action.pending);
  useEffect(() => () => transfer.current?.abort(), []);
  useEffect(() => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    setPreview({ file, url, loaded: false });
    return () => URL.revokeObjectURL(url);
  }, [file]);

  function choose(next: File | undefined) {
    if (!next) return;
    if (!imageTypes.split(',').includes(next.type) || !next.size || next.size > 20 * 1024 * 1024) {
      setPhotoError('请选择 20 MiB 以内的 JPG、PNG、WebP 或 GIF 图片。');
      return;
    }
    setPhotoError('');
    action.clearError();
    upload.current = undefined;
    setFile(next);
  }
  function close() {
    if (action.pending) return;
    if (dirty && mode !== 'portrait') setDiscard(true);
    else onClose();
  }
  async function preparePhoto(source: File) {
    let current = upload.current;
    if (!current || (!current.sent && Date.parse(current.ticket.uploadExpiresAt) <= Date.now())) {
      setStage('正在准备上传…');
      current = { ticket: await requestUpload(root, source, source.name, [], true), sent: false, ready: false };
      upload.current = current;
    }
    if (!current.sent) {
      setStage('正在上传形象照片…');
      const controller = new AbortController();
      transfer.current = controller;
      try { await transferMediaFile(current.ticket, source, controller); }
      finally { transfer.current = undefined; }
      current.sent = true;
    }
    if (!current.ready) {
      setStage('正在确认形象照片…');
      try { await confirmUpload(root, current.ticket); }
      catch (error) {
        if (error instanceof ApiError && error.status === 409) current.sent = false;
        throw error;
      }
      current.ready = true;
    }
    return current.ticket.assetId;
  }
  function save(event: FormEvent) {
    event.preventDefault();
    if (!canSave) return;
    void action.run(async () => {
      writeStarted.current = false;
      const portraitAssetId = file ? await preparePhoto(file) : undefined;
      setStage('正在保存档案…');
      writeStarted.current = true;
      const updated = await mediaWrite(root, '/digital-humans' + (human ? '/' + human.digitalHumanId : ''),
        human ? 'PATCH' : 'POST', decodeDigitalHuman, {
          ...(mode !== 'portrait' && { name: name.trim(), description: human ? description.trim() : description.trim() || null }),
          ...(portraitAssetId && { portraitAssetId }),
        });
      if (human && updated.digitalHumanId !== human.digitalHumanId) throw new Error('保存结果不符');
      onSaved(updated);
    });
  }

  return <>
    <Modal.Backdrop isOpen isDismissable={false} isKeyboardDismissDisabled={action.pending || discard}
      onOpenChange={(open) => { if (!open) close(); }}>
      <Modal.Container scroll="inside"><Modal.Dialog className="media-dialog">
        <Modal.Header><Modal.Heading>{title}</Modal.Heading></Modal.Header>
        <Modal.Body><form className="media-form" onSubmit={save} aria-label="档案资料">
          {mode !== 'portrait' && <TextField value={name} isDisabled={action.pending} onChange={setName}>
            <Label>数字人名称</Label><Input maxLength={120} autoFocus />
          </TextField>}
          {mode !== 'edit' && <>
            {mode === 'portrait' && <p>{human?.name}</p>}
            <div className="media-portrait-editor">
              {file && preview?.file === file ? <img className="media-portrait" src={preview.url} alt="形象照片预览"
                onLoad={() => setPreview({ ...preview, loaded: true })}
                onError={() => { setPhotoError('无法打开这张图片，请重新选择。'); setFile(undefined); }} />
                : <PortraitTile url={human?.portraitUrl} />}
              <div className="media-stack">
                <input ref={chooser} type="file" hidden accept={imageTypes} onChange={(event) => {
                  choose(event.currentTarget.files?.[0]); event.currentTarget.value = '';
                }} />
                <Button variant="secondary" isDisabled={action.pending} onPress={() => chooser.current?.click()}>
                  {file || human?.portraitUrl ? '更换照片' : '选择照片'}
                </Button>
                <span className="text-muted [overflow-wrap:anywhere]">{file?.name ?? (mode === 'create' ? '形象照片 · 选填' : '保存后更新档案卡片')}</span>
              </div>
            </div>
            <ActionError message={photoError} />
          </>}
          {mode !== 'portrait' && <TextField value={description} isDisabled={action.pending} onChange={setDescription}>
            <Label>人设说明</Label><TextArea rows={3} maxLength={2000} />
          </TextField>}
          {action.pending && <p role="status">{stage}</p>}
          <ActionError message={action.error} />
          {mode === 'create' && action.uncertain && writeStarted.current && <p>再次提交前，请先核对是否已创建同名档案。</p>}
          <div className="media-actions justify-end">
            <Button variant="secondary" isDisabled={action.pending} onPress={close}>取消</Button>
            <Button type="submit" isDisabled={!canSave} isPending={action.pending}>
              {mode === 'create' ? '创建档案' : mode === 'portrait' ? '保存照片' : '保存资料'}
            </Button>
          </div>
        </form></Modal.Body>
      </Modal.Dialog></Modal.Container>
    </Modal.Backdrop>
    <DiscardDialog open={discard} description="尚未保存的档案资料将清除。" onKeep={() => setDiscard(false)} onDiscard={onClose} />
  </>;
}
