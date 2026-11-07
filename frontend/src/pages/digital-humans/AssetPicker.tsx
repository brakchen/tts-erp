import { useState } from 'react';
import { Button, Checkbox, Chip, Input, Label, Modal, TextField } from '@heroui/react';
import AssetCard from '../media-assets/AssetCard';
import AssetPreview from '../media-assets/AssetPreview';
import { assetKinds, decodeAssetList, type Asset, type DigitalHuman } from '../media-assets/contracts';
import { CursorTail, useCursorFeed } from '../media-assets/CursorFeed';
import {
  ActionError, AssetTags, DiscardDialog, MediaSelect, ReadStatus, assetType, useLeaveGuard, useMediaRead,
} from '../media-assets/media-ui';

export interface PickerDraft {
  keyword: string; query: string; kind: string; appliedKind: string; hideAssociated: boolean; selected: Asset[];
}
export const emptyPickerDraft: PickerDraft = { keyword: '', query: '', kind: '', appliedKind: '', hideAssociated: true, selected: [] };

export default function AssetPicker({ root, human, pending, error, notice, onAdd, onClose, onUpload, draft, onDraft, targetRead, revision, uploading = false }: {
  root: string; human: DigitalHuman; pending: boolean; error: string; notice: string;
  onAdd: (assets: readonly Asset[]) => void; onClose: () => void; onUpload: () => void;
  draft: PickerDraft; onDraft: (draft: PickerDraft) => void;
  revision: number;
  uploading?: boolean;
  targetRead: { loading: boolean; error?: string; retry: () => void };
}) {
  const [discard, setDiscard] = useState(false);
  const { keyword, query, kind, appliedKind, hideAssociated, selected } = draft;
  const toAdd = selected.filter((asset) => !human.assetIds.includes(asset.assetId));
  const disabled = pending || targetRead.loading || Boolean(targetRead.error);
  useLeaveGuard(Boolean(toAdd.length) || pending);
  function change(values: Partial<PickerDraft>) { onDraft({ ...draft, ...values }); }
  function toggle(asset: Asset, checked: boolean) {
    change({ selected: checked ? [...selected.filter((item) => item.assetId !== asset.assetId), asset] : selected.filter((item) => item.assetId !== asset.assetId) });
  }
  function close() {
    if (pending || targetRead.loading) return;
    if (toAdd.length) setDiscard(true); else onClose();
  }
  const params = new URLSearchParams({ status: 'ready' });
  if (query) params.set('keyword', query);
  if (appliedKind) params.set('kind', appliedKind);
  if (hideAssociated) params.set('excludeDigitalHumanId', human.digitalHumanId);
  const associationRevision = human.assetIds.join(',');
  // The server excludes existing associations before pagination. New draft picks stay visible.
  const read = useCursorFeed(root, '/assets?' + params, decodeAssetList, associationRevision + ':' + revision);
  const associatedParams = new URLSearchParams({ status: 'ready', digitalHumanId: human.digitalHumanId, limit: '1' });
  if (query) associatedParams.set('keyword', query);
  if (appliedKind) associatedParams.set('kind', appliedKind);
  // Association identity, not its length, invalidates both server-filtered results.
  const associated = useMediaRead(root, hideAssociated ? '/assets?' + associatedParams : null, decodeAssetList, associationRevision);
  const hiddenCount = associated.data?.total;
  const hasHidden = hiddenCount !== undefined && String(hiddenCount) !== '0';
  const rows = read.data?.items ?? [];
  return <>
    <Modal.Backdrop isOpen isDismissable={false} isKeyboardDismissDisabled={pending || discard || uploading}
      onOpenChange={(open) => { if (!open) close(); }}>
      <Modal.Container scroll="inside" className="media-picker-container"><Modal.Dialog className="media-dialog media-picker-dialog">
        <Modal.Header className="media-picker-heading">
          <Modal.Heading>{'为「' + human.name + '」添加素材'}</Modal.Heading>
          <Button variant="secondary" isDisabled={disabled} aria-haspopup="dialog" onPress={onUpload}>上传新素材</Button>
        </Modal.Header>
        <Modal.Body className="media-stack">
      {notice && <p role="status" className="media-status">{notice}</p>}
      <form className="media-filters" onSubmit={(event) => {
        event.preventDefault(); change({ query: keyword.trim(), appliedKind: kind });
      }}>
        <TextField value={keyword} onChange={(value) => change({ keyword: value })} isDisabled={disabled}>
          <Label>搜索素材</Label><Input placeholder="名称、说明或标签" maxLength={120} />
        </TextField>
        <MediaSelect label="素材类型" value={kind} options={assetKinds} disabled={disabled}
          onChange={(value) => change({ kind: value })} />
        <Button type="submit" isDisabled={disabled}>搜索</Button>
      </form>
      <div className="media-picker-toolbar flex flex-wrap items-center justify-between gap-2" data-picker-toolbar>
        <div className="flex flex-wrap items-center gap-2">
          <Checkbox isSelected={hideAssociated} isDisabled={disabled}
            onChange={(checked) => change({ hideAssociated: checked })}>
            <Checkbox.Content><Checkbox.Control className="media-checkbox-control"><Checkbox.Indicator /></Checkbox.Control><Label>隐藏已选内容</Label></Checkbox.Content>
          </Checkbox>
          {hideAssociated && hasHidden && <span className="text-muted">已隐藏 {hiddenCount} 项已添加素材</span>}
        </div>
      </div>
      <ReadStatus title="目标档案" loading={targetRead.loading} error={targetRead.error} onRetry={targetRead.retry} />
      {hideAssociated && <ReadStatus title="已添加素材数量" loading={associated.loading} error={associated.error} onRetry={associated.retry} />}
      <ActionError message={error ? '提交未完成，待添加的勾选已保留。' + error : ''} />
      {toAdd.length > 0 && <div className="media-picker-choices" role="group" aria-label="本次勾选素材">
        {toAdd.map((asset) => <Button key={asset.assetId} size="sm" variant="ghost" className="media-back" isDisabled={disabled}
          onPress={() => toggle(asset, false)}>取消勾选 {asset.filename}</Button>)}
      </div>}
      <div data-feed-scroll className="media-picker-results" role="region" aria-label="可选素材结果" tabIndex={0}>
      <ReadStatus title="可选素材" loading={read.loading} error={!read.data ? read.error : undefined} onRetry={read.retry} />
      {read.data && !rows.length && <p className="media-box">{hideAssociated && hasHidden
        ? '符合条件的素材均已添加。取消勾选“隐藏已选内容”可查看。'
        : hideAssociated && hiddenCount === undefined ? '当前没有可新增的匹配素材。' : '没有符合条件的素材。'}</p>}
      <ul className="media-grid" data-media-grid aria-label="可选素材">
        {rows.map((asset) => {
          const alreadyAdded = human.assetIds.includes(asset.assetId);
          const statusId = 'linked-asset-' + asset.assetId;
          return <li key={asset.assetId} className="min-w-0"><AssetCard asset={asset} root={root}>
            <Checkbox isSelected={alreadyAdded || toAdd.some((item) => item.assetId === asset.assetId)}
              isDisabled={alreadyAdded || disabled || read.loading || asset.status !== 'ready'}
              aria-describedby={alreadyAdded ? statusId : undefined} onChange={(checked) => toggle(asset, checked)}>
              <Checkbox.Content><Checkbox.Control className="media-checkbox-control"><Checkbox.Indicator /></Checkbox.Control>
                <Label className="min-w-0 [overflow-wrap:anywhere]">{asset.filename}</Label></Checkbox.Content>
            </Checkbox>
            <Chip size="sm" className="media-kind"><Chip.Label>{assetType(asset)}</Chip.Label></Chip>
            {alreadyAdded && <Chip id={statusId} size="sm" className="media-chip max-w-full"><Chip.Label className="whitespace-normal [overflow-wrap:anywhere]">已添加至「{human.name}」</Chip.Label></Chip>}
            <AssetTags tags={asset.tags} />
            <details className="media-card-preview">
              <summary>查看预览</summary>
              <AssetPreview asset={asset} root={root} />
              {asset.description && <p className="text-muted whitespace-pre-wrap">{asset.description}</p>}
            </details>
          </AssetCard></li>;
        })}
      </ul>
      <CursorTail {...read} paused={disabled || uploading || discard} />
      </div>
      </Modal.Body>
      <Modal.Footer className="media-picker-actions">
        <strong aria-live="polite">本次新增 {toAdd.length} 项</strong>
        <Button variant="secondary" isDisabled={pending} onPress={close}>取消</Button>
        <Button isDisabled={!toAdd.length || disabled} isPending={pending} onPress={() => onAdd(toAdd)}>确认添加（{toAdd.length}）</Button>
      </Modal.Footer>
      </Modal.Dialog></Modal.Container>
    </Modal.Backdrop>
    <DiscardDialog open={discard} description="本次勾选的素材尚未添加，关闭后将清除这些选择。"
      onKeep={() => setDiscard(false)} onDiscard={onClose} />
  </>;
}
