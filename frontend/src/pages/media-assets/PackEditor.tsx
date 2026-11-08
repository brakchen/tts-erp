import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Label, Modal, TextField } from '@heroui/react';
import { ApiError } from '../../shared/api/client';
import { CursorTail, useCursorFeed } from './CursorFeed';
import { decodeAssetList, decodeMediaPack, type MediaPack, type PackReference, type UploadTicket } from './contracts';
import { confirmUpload, requestUpload, transferMediaFile, videoTypes } from './media-upload';
import { ActionError, DiscardDialog, ReadStatus, mediaWrite, uncertainResult, useLeaveGuard, useMediaAction } from './media-ui';

type QueuedVideo = { file: File; ticket?: UploadTicket; sent?: boolean; readyId?: string };
const maximumVideosPerWrite = 200;

async function uploadVideo(root: string, item: QueuedVideo, controller: AbortController, progress: (value: number) => void) {
  if (!item.ticket || (!item.sent && Date.parse(item.ticket.uploadExpiresAt) <= Date.now())) {
    item.ticket = await requestUpload(root, item.file, item.file.name.slice(0, 255), []);
  }
  if (!item.sent) {
    await transferMediaFile(item.ticket, item.file, controller, progress);
    item.sent = true;
  }
  try {
    await confirmUpload(root, item.ticket);
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 409 &&
      (failure.detail === 'UPLOAD_NOT_FOUND' || failure.detail === 'SIZE_MISMATCH')) {
      item.ticket = undefined;
      item.sent = false;
    }
    throw failure;
  }
  item.readyId = item.ticket.assetId;
}

export default function PackEditor({ root, target, human, initialAssetIds = [], onDone, onClose }: {
  root: string;
  target?: PackReference;
  human?: { id: string; name: string };
  initialAssetIds?: string[];
  onDone: (pack: MediaPack) => void;
  onClose: (uploaded: boolean) => void;
}) {
  const [name, setName] = useState('');
  const [picked, setPicked] = useState(initialAssetIds);
  const [queue, setQueue] = useState<QueuedVideo[]>([]);
  const [readyCount, setReadyCount] = useState(0);
  const [transferStatus, setTransferStatus] = useState('');
  const [fileError, setFileError] = useState('');
  const [discard, setDiscard] = useState(false);
  const [uncertainSave, setUncertainSave] = useState(false);
  const token = useRef<string | undefined>(undefined);
  const input = useRef<HTMLInputElement>(null);
  const transfer = useRef<AbortController | undefined>(undefined);
  const action = useMediaAction();
  const [keyword, setKeyword] = useState('');
  const [appliedKeyword, setAppliedKeyword] = useState('');
  const params = new URLSearchParams({ kind: 'video', status: 'ready' });
  if (target) params.set('excludePackId', target.packId);
  if (appliedKeyword) params.set('keyword', appliedKeyword);
  const feed = useCursorFeed(root, '/assets?' + params, decodeAssetList);
  const total = picked.length + queue.length;
  const overLimit = total > maximumVideosPerWrite;
  const dirty = Boolean(name.trim() || total);
  const locked = action.pending || uncertainSave;
  const canSave = !action.pending && !overLimit && Boolean(total && (target || name.trim()));
  useLeaveGuard(dirty || action.pending);
  useEffect(() => () => transfer.current?.abort(), []);

  function close() {
    if (action.pending) return;
    if (dirty) setDiscard(true);
    else onClose(readyCount > 0);
  }

  function chooseFiles(files: File[]) {
    if (!files.length) return;
    const supported = videoTypes.split(',');
    if (files.some((file) => !file.size || !supported.includes(file.type))) {
      setFileError('请选择非空的 MP4、WebM 或 MOV 视频。');
      return;
    }
    setFileError('');
    setQueue((previous) => [...previous, ...files.map((file) => ({ file }))]);
  }

  function save() {
    if (!canSave) return;
    void action.run(async () => {
      try {
        for (const item of queue) {
          if (item.readyId) continue;
          const controller = new AbortController();
          transfer.current = controller;
          setTransferStatus('正在上传 ' + item.file.name + '…');
          await uploadVideo(root, item, controller, (percent) => setTransferStatus(
            percent === 100 ? '正在确认入库 ' + item.file.name + '…' : '正在上传 ' + item.file.name + ' · ' + percent + '%'
          ));
          setReadyCount(queue.filter((entry) => entry.readyId).length);
        }
      } finally {
        transfer.current = undefined;
        setTransferStatus('');
      }
      const assetIds = [...new Set([...picked, ...queue.flatMap((item) => item.readyId ? [item.readyId] : [])])];
      try {
        if (!target) token.current ??= crypto.randomUUID();
        const result = await mediaWrite(root, target ? '/packs/' + target.packId + '/videos' : '/packs', 'POST', decodeMediaPack,
          target ? { assetIds } : { name: name.trim(), assetIds, creationToken: token.current, ...(human && { digitalHumanId: human.id }) });
        if (target && result.packId !== target.packId) throw new Error('素材包结果不符');
        onDone(result);
      } catch (failure) {
        if (!target) setUncertainSave(uncertainResult(failure));
        throw failure;
      }
    });
  }

  return <>
    <Modal.Backdrop isOpen isDismissable={false} isKeyboardDismissDisabled={action.pending || discard}
      onOpenChange={(open) => { if (!open) close(); }}>
      <Modal.Container size="lg" scroll="inside"><Modal.Dialog>
        <Modal.Header>
          <Modal.Heading>{target ? '添加视频' : '新建素材包'}</Modal.Heading>
          {target && <p>添加到「{target.name}」</p>}
          {human && <p>保存后关联到「{human.name}」</p>}
        </Modal.Header>
        <Modal.Body className="media-stack">
          {!target && <TextField value={name} onChange={setName} isDisabled={locked}>
            <Label>包名称</Label><Input maxLength={80} placeholder="例如：室内带货" />
          </TextField>}
          <form className="media-actions" onSubmit={(event) => {
            event.preventDefault();
            setAppliedKeyword(keyword.trim());
          }}>
            <TextField value={keyword} onChange={setKeyword} isDisabled={locked}>
              <Label>选择库内视频</Label><Input maxLength={120} placeholder="搜索视频" />
            </TextField>
            <Button type="submit" variant="secondary" isDisabled={locked}>搜索</Button>
          </form>
          <ReadStatus title="库内视频" loading={feed.loading} error={!feed.data ? feed.error : undefined} onRetry={feed.retry} />
          {feed.data && !feed.data.items.length && <p className="text-muted">没有可添加的视频。</p>}
          <ul className="media-pack-options" aria-label="库内视频" data-feed-scroll>
            {feed.data?.items.map((asset) => <li key={asset.assetId}>
              <Checkbox isDisabled={locked} isSelected={picked.includes(asset.assetId)} onChange={(selected) =>
                setPicked((previous) => selected ? [...previous, asset.assetId] : previous.filter((id) => id !== asset.assetId))}>
                <Checkbox.Content><Checkbox.Control><Checkbox.Indicator /></Checkbox.Control><Label>{asset.filename}</Label></Checkbox.Content>
              </Checkbox>
            </li>)}
            <li><CursorTail {...feed} paused={locked} /></li>
          </ul>
          <div className="media-stack">
            <Label htmlFor="pack-video-files">上传新视频</Label>
            <input ref={input} hidden id="pack-video-files" type="file" multiple accept={videoTypes} disabled={locked}
              onChange={(event) => { chooseFiles(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = ''; }} />
            <Button variant="secondary" className="self-start" isDisabled={locked} onPress={() => input.current?.click()}>选择视频文件</Button>
            {queue.length > 0 && <ul className="media-stack" aria-label="待上传视频">{queue.map((item, index) => <li key={index} className="media-actions">
              <span className="min-w-0 [overflow-wrap:anywhere]">{item.file.name}{item.readyId && ' · 已入库'}</span>
              {!item.readyId && <Button size="sm" variant="ghost" isDisabled={locked} aria-label={'取消上传 ' + item.file.name}
                onPress={() => setQueue((previous) => previous.filter((entry) => entry !== item))}>移出</Button>}
            </li>)}</ul>}
          </div>
          <ActionError message={fileError || (overLimit ? '每次最多添加 ' + maximumVideosPerWrite + ' 段视频。' : '')} />
          {transferStatus && <p role="status">{transferStatus}</p>}
          <ActionError message={uncertainSave ? '保存结果尚未确认。重试会继续同一次建包。' : action.error} />
          {readyCount > 0 && <p className="text-muted">已入库 {readyCount} 段。重试会保留这些视频。</p>}
        </Modal.Body>
        <Modal.Footer className="media-pack-footer">
          <span role="status">已选 {total} 段视频</span>
          <div className="media-actions">
            <Button variant="secondary" isDisabled={action.pending} onPress={close}>取消</Button>
            <Button isDisabled={!canSave} isPending={action.pending} onPress={save}>{target ? '添加到素材包' : '创建素材包'}</Button>
          </div>
        </Modal.Footer>
      </Modal.Dialog></Modal.Container>
    </Modal.Backdrop>
    <DiscardDialog open={discard} description="未保存的组包选择会清除。已入库的视频仍保留在公共资产库。"
      onKeep={() => setDiscard(false)} onDiscard={() => onClose(readyCount > 0 || uncertainSave)} />
  </>;
}
