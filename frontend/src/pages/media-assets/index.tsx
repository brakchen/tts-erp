import { useState, type FormEvent } from 'react';
import { Button, Chip, Input, Label, Modal, TextArea, TextField } from '@heroui/react';
import { ApiError } from '../../shared/api/client';
import type { FrontendBootstrap, PageProps } from '../../shared/contracts';
import AssetCard from './AssetCard';
import AssetReferences from './AssetReferences';
import { CursorTail, useCursorFeed } from './CursorFeed';
import OperationConfirmation from './OperationConfirmation';
import TagEditor, { withPendingTag } from './TagEditor';
import UploadAsset from './UploadAsset';
import ViewAsset from './ViewAsset';
import { assetKinds, decodeAsset, decodeAssetList, type Asset } from './contracts';
import {
  ActionError, AssetTags, DiscardDialog, MediaSelect, ReadStatus, assetStatus, assetType,
  mediaWrite, recordLink, useLeaveGuard, useMediaAction, useMediaRead, useRecordSelection,
} from './media-ui';

const emptyFilters = { keyword: '', kind: '' };

function DeleteAsset({ bootstrap, asset, onDone, onClose }: {
  bootstrap: FrontendBootstrap; asset: Asset; onDone: () => void; onClose: () => void;
}) {
  const root = bootstrap.rootPath;
  const action = useMediaAction();
  const read = useMediaRead(root, '/assets/' + asset.assetId, decodeAsset);
  const references = read.data?.usedBy ?? null;
  const inUse = Boolean(references?.length);
  return <OperationConfirmation title={'删除素材「' + asset.filename + '」'}
    description={inUse ? '该素材已添加至数字人档案。请解除关联后再删除。' : '删除后，该素材将从全局素材库移除。'}
    pending={action.pending} error={action.error} confirmDisabled={!references || read.loading || inUse}
    onCancel={onClose} onConfirm={async () => {
      try {
        await action.run(async () => {
          await mediaWrite(root, '/assets/' + asset.assetId, 'DELETE', () => undefined);
          onDone();
        }, true);
      } catch (failure) {
        if (failure instanceof ApiError && failure.status === 409) read.retry();
      }
    }}>
    <ReadStatus title="素材引用" loading={read.loading}
      error={read.error ?? (!read.loading && !references ? '无法确认当前关联。' : undefined)} onRetry={read.retry} />
    {inUse && !read.loading && <AssetReferences bootstrap={bootstrap} references={references} />}
  </OperationConfirmation>;
}

function EditAsset({ root, asset, onDone, onClose }: {
  root: string; asset: Asset; onDone: () => void; onClose: () => void;
}) {
  const [draft, setDraft] = useState({ filename: asset.filename, description: asset.description ?? '', tags: asset.tags, tagInput: '' });
  const [catalog, setCatalog] = useState<string[]>();
  const [discard, setDiscard] = useState(false);
  const action = useMediaAction();
  const dirty = draft.filename !== asset.filename || draft.description !== (asset.description ?? '')
    || JSON.stringify(draft.tags) !== JSON.stringify(asset.tags) || Boolean(draft.tagInput.trim());
  const canSave = dirty && Boolean(draft.filename.trim()) && (!draft.tagInput.trim() || catalog !== undefined);
  useLeaveGuard(dirty || action.pending);
  function close() {
    if (!action.pending) { if (dirty) setDiscard(true); else onClose(); }
  }
  function save(event: FormEvent) {
    event.preventDefault();
    if (!canSave) return;
    const tags = withPendingTag(draft.tags, draft.tagInput, catalog ?? []);
    void action.run(async () => {
      const response = await mediaWrite(root, '/assets/' + asset.assetId, 'PATCH', decodeAsset, {
        ...(draft.filename !== asset.filename && { filename: draft.filename.trim() }),
        ...(draft.description !== (asset.description ?? '') && { description: draft.description.trim() }),
        ...(JSON.stringify(tags) !== JSON.stringify(asset.tags) && { tags }),
      });
      if (response.assetId !== asset.assetId) throw new Error('保存结果不符');
      onDone();
    });
  }
  return <>
    <Modal.Backdrop isOpen isDismissable={false} isKeyboardDismissDisabled={action.pending || discard}
      onOpenChange={(open) => { if (!open) close(); }}>
      <Modal.Container scroll="inside"><Modal.Dialog className="media-dialog">
        <Modal.Header><Modal.Heading>编辑素材</Modal.Heading></Modal.Header>
        <Modal.Body><form className="media-form" onSubmit={save}>
          <TextField value={draft.filename} isDisabled={action.pending}
            onChange={(filename) => setDraft((previous) => ({ ...previous, filename }))}>
            <Label>素材名称</Label><Input maxLength={255} />
          </TextField>
          <TextField value={draft.description} isDisabled={action.pending}
            onChange={(description) => setDraft((previous) => ({ ...previous, description }))}>
            <Label>素材说明</Label><TextArea rows={3} maxLength={2000} />
          </TextField>
          <TagEditor root={root} selected={draft.tags} input={draft.tagInput} onCatalog={setCatalog} disabled={action.pending}
            onChange={(tags) => setDraft((previous) => ({ ...previous, tags }))}
            onInput={(tagInput) => setDraft((previous) => ({ ...previous, tagInput }))} />
          <ActionError message={action.error && (action.uncertain ? action.error : '保存未完成。' + action.error)} />
          <div className="media-actions justify-end">
            <Button variant="secondary" isDisabled={action.pending} onPress={close}>取消</Button>
            <Button type="submit" isDisabled={!canSave} isPending={action.pending}>保存资料</Button>
          </div>
        </form></Modal.Body>
      </Modal.Dialog></Modal.Container>
    </Modal.Backdrop>
    <DiscardDialog open={discard} description="本次修改尚未保存，放弃后将清除这些输入。"
      onKeep={() => setDiscard(false)} onDiscard={onClose} />
  </>;
}

export default function MediaAssets({ bootstrap }: PageProps) {
  const root = bootstrap.rootPath;
  const canWrite = bootstrap.roleCapabilities?.readwrite === true;
  const [filters, setFilters] = useState(emptyFilters);
  const [applied, setApplied] = useState(emptyFilters);
  const [revision, setRevision] = useState(0);
  const [selected, select] = useRecordSelection('asset');
  const [editing, setEditing] = useState<Asset>();
  const [deleting, setDeleting] = useState<Asset>();
  const [upload, setUpload] = useState(false);
  const [notice, setNotice] = useState<{ message: string; assetId?: string }>();
  const params = new URLSearchParams();
  if (applied.keyword) params.set('keyword', applied.keyword);
  if (applied.kind) params.set('kind', applied.kind);
  const feed = useCursorFeed(root, '/assets?' + params, decodeAssetList, revision);
  const assets = feed.data?.items ?? [];
  const uploadedAssetId = notice?.assetId;

  function changed(message: string, assetId?: string) {
    setRevision((value) => value + 1);
    setNotice({ message, assetId });
  }
  function closeView() {
    const fromHuman = new URLSearchParams(window.location.search).get('fromHuman');
    const link = fromHuman && /^[1-9]\d*$/.test(fromHuman) && recordLink(bootstrap, 'digital-humans', 'human', fromHuman);
    if (link) window.location.assign(link);
    else select(null);
  }
  return <div className="erp-page media-workspace">
    <div className="media-library-toolbar">
      <form className="media-filters" onSubmit={(event) => {
        event.preventDefault();
        setApplied({ keyword: filters.keyword.trim(), kind: filters.kind });
        setRevision((value) => value + 1);
        setNotice(undefined);
      }}>
        <TextField value={filters.keyword} onChange={(keyword) => setFilters({ ...filters, keyword })}>
          <Label>搜索素材</Label><Input placeholder="名称、说明或标签" maxLength={120} />
        </TextField>
        <MediaSelect label="素材类型" value={filters.kind} options={assetKinds}
          onChange={(kind) => setFilters({ ...filters, kind })} />
        <Button type="submit" variant="secondary">搜索</Button>
      </form>
      {canWrite && <Button aria-haspopup="dialog" onPress={() => setUpload(true)}>上传素材</Button>}
    </div>
    {notice && <div className="media-status media-actions">
      <p role="status">{notice.message}</p>
      {uploadedAssetId && <Button variant="secondary" size="sm" onPress={() => select(uploadedAssetId)}>查看刚上传的素材</Button>}
    </div>}
    <ReadStatus title="素材列表" loading={feed.loading} error={!feed.data ? feed.error : undefined} onRetry={feed.retry} />
    {feed.data && !assets.length && <p className="media-box">没有符合条件的素材。</p>}
    <ul className="media-grid" data-media-grid aria-label="素材列表">
      {assets.map((asset) => <li key={asset.assetId} className="min-w-0"><AssetCard asset={asset} root={root}>
        <div className="media-card-title">
          <h3 title={asset.filename}>{asset.filename}</h3>
          <Chip size="sm" className="media-kind"><Chip.Label>{assetType(asset)}</Chip.Label></Chip>
        </div>
        {asset.status !== 'ready' && <span className="text-muted">{assetStatus(asset.status)}</span>}
        <AssetTags tags={asset.tags} />
        <div className="media-card-actions">
          <Button variant="ghost" aria-label={'查看 ' + asset.filename} onPress={() => select(asset.assetId)}>查看</Button>
          {canWrite && <>
            <Button variant="ghost" aria-label={'编辑 ' + asset.filename} onPress={() => setEditing(asset)}>编辑</Button>
            <Button variant="ghost" className="text-danger" aria-label={'删除 ' + asset.filename} onPress={() => setDeleting(asset)}>删除</Button>
          </>}
        </div>
      </AssetCard></li>)}
    </ul>
    <CursorTail {...feed} paused={Boolean(upload || selected || editing || deleting)} />
    {selected && <ViewAsset key={selected} bootstrap={bootstrap} id={selected} onClose={closeView} />}
    {canWrite && editing && <EditAsset root={root} asset={editing} onClose={() => setEditing(undefined)}
      onDone={() => { setEditing(undefined); changed('素材资料已保存。'); document.scrollingElement?.scrollTo(0, 0); }} />}
    {canWrite && deleting && <DeleteAsset bootstrap={bootstrap} asset={deleting} onClose={() => setDeleting(undefined)}
      onDone={() => { setDeleting(undefined); changed('素材已从全局库删除。'); }} />}
    {canWrite && upload && <UploadAsset bootstrap={bootstrap} onClose={() => setUpload(false)}
      onComplete={(id, filename) => { setUpload(false); changed('已入库「' + filename + '」。', id); document.scrollingElement?.scrollTo(0, 0); }} />}
  </div>;
}
