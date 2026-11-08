import { useState } from 'react';
import { Button, Card, Modal } from '@heroui/react';
import type { FrontendBootstrap } from '../../shared/contracts';
import AssetCard from './AssetCard';
import { CursorTail, useCursorFeed } from './CursorFeed';
import OperationConfirmation from './OperationConfirmation';
import PackEditor from './PackEditor';
import ViewAsset from './ViewAsset';
import { decodeAssetList, decodeMediaPack, decodeMediaPackFeed, type Asset, type DigitalHuman, type MediaPack } from './contracts';
import { ActionError, ReadStatus, mediaWrite, useMediaAction, useMediaRead } from './media-ui';

export function PackPicker({ root, human, assetIds, onClose, onCreate, onDone }: {
  root: string;
  human?: DigitalHuman;
  assetIds?: string[];
  onClose: () => void;
  onCreate?: () => void;
  onDone: (pack: MediaPack) => void;
}) {
  const [selected, select] = useState<MediaPack>();
  const action = useMediaAction();
  const path = human ? '/packs?excludeDigitalHumanId=' + human.digitalHumanId : '/packs';
  const feed = useCursorFeed(root, path, decodeMediaPackFeed);
  const items = feed.data?.items ?? [];
  function save() {
    if (!selected) return;
    void action.run(async () => {
      const pack = await mediaWrite(root, human ? '/digital-humans/' + human.digitalHumanId + '/packs' : '/packs/' + selected.packId + '/videos',
        'POST', decodeMediaPack, human ? { packId: selected.packId } : { assetIds });
      if (pack.packId !== selected.packId) throw new Error('素材包结果不符');
      onDone(pack);
    });
  }
  return <Modal.Backdrop isOpen isDismissable={false} isKeyboardDismissDisabled={action.pending}
    onOpenChange={(open) => { if (!open && !action.pending) onClose(); }}>
    <Modal.Container scroll="inside"><Modal.Dialog>
      <Modal.Header><Modal.Heading>{human ? '添加素材包' : '加入已有包'}</Modal.Heading>
        <p>{human ? '为「' + human.name + '」选择公共素材包' : '已选 ' + assetIds?.length + ' 段视频'}</p>
      </Modal.Header>
      <Modal.Body className="media-stack">
        <ReadStatus title="公共素材包" loading={feed.loading} error={!feed.data ? feed.error : undefined} onRetry={feed.retry} />
        {feed.data && !items.length && <p>没有可选择的素材包。</p>}
        <ul className="media-pack-options" aria-label="已有素材包" data-feed-scroll>{items.map((pack) => <li key={pack.packId}>
          <Button variant={selected?.packId === pack.packId ? 'primary' : 'secondary'} className="media-pack-choice"
            isDisabled={action.pending} aria-pressed={selected?.packId === pack.packId} onPress={() => select(pack)}>
            <span className="min-w-0 [overflow-wrap:anywhere]">{pack.name}</span><span className="shrink-0">{pack.videoCount} 段视频</span>
          </Button>
        </li>)}<li><CursorTail {...feed} paused={action.pending} /></li></ul>
        <ActionError message={action.error} />
      </Modal.Body>
      <Modal.Footer className="media-pack-footer">
        {onCreate && <Button variant="secondary" isDisabled={action.pending} onPress={onCreate}>新建素材包</Button>}
        <div className="media-actions">
          <Button variant="secondary" isDisabled={action.pending} onPress={onClose}>取消</Button>
          <Button isDisabled={!selected || action.pending} isPending={action.pending} onPress={save}>{human ? '确认关联' : '确认加入'}</Button>
        </div>
      </Modal.Footer>
    </Modal.Dialog></Modal.Container>
  </Modal.Backdrop>;
}

export function PackCatalog({ bootstrap, canWrite, human, revision, onOpen }: {
  bootstrap: FrontendBootstrap;
  canWrite: boolean;
  human?: DigitalHuman;
  revision: number;
  onOpen: (id: string) => void;
}) {
  const root = bootstrap.rootPath;
  const [changes, setChanges] = useState(0);
  const [mode, setMode] = useState<'create' | 'attach'>();
  const [detaching, setDetaching] = useState<MediaPack>();
  const [notice, setNotice] = useState('');
  const action = useMediaAction();
  const path = human ? '/packs?digitalHumanId=' + human.digitalHumanId : '/packs';
  const feed = useCursorFeed(root, path, decodeMediaPackFeed, revision + ':' + changes);
  const items = feed.data?.items ?? [];
  const target = human && { id: human.digitalHumanId, name: human.name };

  function saved(pack: MediaPack) {
    setMode(undefined);
    setChanges((current) => current + 1);
    if (human) setNotice('已关联「' + pack.name + '」。');
    else onOpen(pack.packId);
  }

  return <section className="media-stack" aria-label="素材包">
    <div className="media-list-toolbar">
      <h2>素材包 {feed.data && <span className="text-muted">· {feed.data.total} 个</span>}</h2>
      {canWrite && (!human || human.status === 'active') && <Button aria-haspopup="dialog"
        onPress={() => setMode(human ? 'attach' : 'create')}>{human ? '添加素材包' : '新建素材包'}</Button>}
    </div>
    {notice && <p className="media-status" role="status">{notice}</p>}
    <ReadStatus title="素材包" loading={feed.loading} error={!feed.data ? feed.error : undefined} onRetry={feed.retry} />
    {feed.data && !items.length && <p className="media-box">{human ? '还没有关联素材包。' : '资产库还没有素材包。'}</p>}
    <ul className="media-grid" data-media-grid aria-label="素材包列表">
      {items.map((pack) => <li key={pack.packId} className="min-w-0">
        <Card className="media-box media-card shadow-none"><Card.Content className="media-card-content">
          <h3 className="font-semibold [overflow-wrap:anywhere]">{pack.name}</h3>
          <p className="text-muted">{pack.videoCount} 段视频</p>
          <div className="media-actions">
            <Button variant="secondary" aria-label={'打开素材包 ' + pack.name} onPress={() => onOpen(pack.packId)}>打开素材包</Button>
            {canWrite && human && <Button variant="ghost" aria-label={'解除关联 ' + pack.name}
              onPress={() => { action.clearError(); setDetaching(pack); }}>解除关联</Button>}
          </div>
        </Card.Content></Card>
      </li>)}
    </ul>
    <CursorTail {...feed} paused={Boolean(mode || detaching)} />
    {mode === 'attach' && human && <PackPicker root={root} human={human} onDone={saved}
      onClose={() => setMode(undefined)} onCreate={() => setMode('create')} />}
    {mode === 'create' && <PackEditor root={root} human={target} onDone={saved} onClose={(refresh) => {
      setMode(undefined);
      if (refresh) setChanges((current) => current + 1);
    }} />}
    {human && detaching && <OperationConfirmation title={'解除关联「' + detaching.name + '」'}
      description="素材包仍保留在公共资产库。" confirmLabel="解除关联" pending={action.pending} error={action.error}
      onCancel={() => setDetaching(undefined)} onConfirm={() => action.run(async () => {
        await mediaWrite(root, '/digital-humans/' + human.digitalHumanId + '/packs/' + detaching.packId, 'DELETE', () => undefined);
        setDetaching(undefined);
        setChanges((current) => current + 1);
        setNotice('已解除关联。素材包仍在公共资产库。');
      })} />}
  </section>;
}

export function PackDetail({ bootstrap, id, canWrite, backLabel, onBack }: {
  bootstrap: FrontendBootstrap;
  id: string;
  canWrite: boolean;
  backLabel: string;
  onBack: () => void;
}) {
  const root = bootstrap.rootPath;
  const [revision, setRevision] = useState(0);
  const [adding, setAdding] = useState(false);
  const [preview, setPreview] = useState<string>();
  const [removing, setRemoving] = useState<Asset>();
  const [notice, setNotice] = useState('');
  const action = useMediaAction();
  const read = useMediaRead(root, '/packs/' + id, decodeMediaPack, revision);
  const pack = read.data;
  const feed = useCursorFeed(root, pack ? '/assets?packId=' + id : null, decodeAssetList, revision);
  const items = feed.data?.items ?? [];

  function changed(message: string) {
    setNotice(message);
    setRevision((current) => current + 1);
  }

  return <section className="media-record">
    <header className="media-title">
      <Button variant="ghost" className="media-back self-start" onPress={onBack}>← {backLabel}</Button>
      <div className="media-list-toolbar">
        <div className="min-w-0 [overflow-wrap:anywhere]">
          <p className="text-muted">公共素材包</p>
          <h1>{pack?.name ?? '素材包'}</h1>
          {pack && <p>{pack.videoCount} 段视频</p>}
        </div>
        {canWrite && pack && <Button aria-haspopup="dialog" onPress={() => setAdding(true)}>添加视频</Button>}
      </div>
    </header>
    {notice && <p className="media-status" role="status">{notice}</p>}
    <ReadStatus title="素材包详情" loading={read.loading} error={read.error} onRetry={read.retry} />
    <ReadStatus title="包内视频" loading={feed.loading} error={!feed.data ? feed.error : undefined} onRetry={feed.retry} />
    {feed.data && !items.length && <p className="media-box">这个素材包里还没有视频。</p>}
    <ul className="media-grid" data-media-grid aria-label="包内视频">
      {items.map((video) => <li key={video.assetId} className="min-w-0"><AssetCard root={root} asset={video}>
        <h3 className="font-semibold [overflow-wrap:anywhere]">{video.filename}</h3>
        <div className="media-card-actions">
          <Button variant="ghost" aria-label={'预览 ' + video.filename} onPress={() => setPreview(video.assetId)}>预览</Button>
          {canWrite && <Button variant="ghost" aria-label={'移出 ' + video.filename}
            onPress={() => { action.clearError(); setRemoving(video); }}>移出</Button>}
        </div>
      </AssetCard></li>)}
    </ul>
    <CursorTail {...feed} paused={Boolean(adding || preview || removing)} />
    {adding && pack && <PackEditor root={root} target={pack} onClose={() => setAdding(false)}
      onDone={() => { setAdding(false); changed('已添加视频。'); }} />}
    {preview && <ViewAsset bootstrap={bootstrap} id={preview} onClose={() => setPreview(undefined)} />}
    {removing && <OperationConfirmation title={'移出「' + removing.filename + '」'}
      description="原视频仍保留在公共资产库。所有关联此包的档案都会看到更新。" confirmLabel="移出视频"
      pending={action.pending} error={action.error} onCancel={() => setRemoving(undefined)}
      onConfirm={() => action.run(async () => {
        await mediaWrite(root, '/packs/' + id + '/videos/' + removing.assetId, 'DELETE', () => undefined);
        setRemoving(undefined);
        changed('已移出视频。原视频仍在公共资产库。');
      })} />}
  </section>;
}
