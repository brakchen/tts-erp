import { useState } from 'react';
import { Button, Checkbox, Chip, Input, Label, TextField } from '@heroui/react';
import AssetCard from '../media-assets/AssetCard';
import AssetPreview from '../media-assets/AssetPreview';
import { assetKinds, decodeAssetList, type Asset, type DigitalHumanDetail } from '../media-assets/contracts';
import {
  ActionError, AssetTags, DiscardDialog, MediaPagination, MediaSelect, ReadStatus, RecordPage,
  mediaPageSize, useLeaveGuard, useMediaRead,
} from '../media-assets/media-ui';

export interface PickerDraft {
  keyword: string; query: string; kind: string; appliedKind: string; offset: number; hideAssociated: boolean; selected: Asset[];
}
export const emptyPickerDraft: PickerDraft = { keyword: '', query: '', kind: '', appliedKind: '', offset: 0, hideAssociated: true, selected: [] };

export default function AssetPicker({ root, human, pending, error, notice, onAdd, onClose, onUpload, draft, onDraft, targetRead, revision }: {
  root: string; human: DigitalHumanDetail; pending: boolean; error: string; notice: string;
  onAdd: (assets: readonly Asset[]) => void; onClose: () => void; onUpload: () => void;
  draft: PickerDraft; onDraft: (draft: PickerDraft) => void;
  revision: number;
  targetRead: { loading: boolean; error?: string; retry: () => void };
}) {
  const [discard, setDiscard] = useState(false);
  const { keyword, query, kind, appliedKind, offset, hideAssociated, selected } = draft;
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
  const params = new URLSearchParams({ status: 'ready', limit: String(mediaPageSize) });
  if (query) params.set('keyword', query);
  if (appliedKind) params.set('kind', appliedKind);
  if (offset) params.set('offset', String(offset));
  if (hideAssociated) params.set('excludeDigitalHumanId', human.digitalHumanId);
  const associationRevision = human.assetIds.join(',');
  // The server excludes existing associations before pagination. New draft picks stay visible.
  const read = useMediaRead(root, '/assets?' + params, decodeAssetList, associationRevision + ':' + revision);
  const associatedParams = new URLSearchParams({ status: 'ready', digitalHumanId: human.digitalHumanId, limit: '1' });
  if (query) associatedParams.set('keyword', query);
  if (appliedKind) associatedParams.set('kind', appliedKind);
  // Association identity, not its length, invalidates both server-filtered results.
  const associated = useMediaRead(root, hideAssociated ? '/assets?' + associatedParams : null, decodeAssetList, associationRevision);
  const hiddenCount = associated.data?.total;
  const hasHidden = hiddenCount !== undefined && String(hiddenCount) !== '0';
  const rows = read.data?.items ?? [];
  return <>
    <RecordPage eyebrow="数字人 / 添加素材" title={'为「' + human.name + '」添加素材'}
      description="勾选要新增的素材。搜索和翻页保留本次勾选。"
      actions={<Button variant="secondary" isDisabled={disabled} aria-haspopup="dialog" onPress={onUpload}>上传新素材</Button>}>
      {notice && <p role="status" className="media-status">{notice}</p>}
      <form className="media-filters" onSubmit={(event) => {
        event.preventDefault(); change({ query: keyword.trim(), appliedKind: kind, offset: 0 });
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
            onChange={(checked) => change({ hideAssociated: checked, offset: 0 })}>
            <Checkbox.Content><Checkbox.Control className="media-checkbox-control"><Checkbox.Indicator /></Checkbox.Control><Label>隐藏已选内容</Label></Checkbox.Content>
          </Checkbox>
          {hideAssociated && hasHidden && <span className="text-muted">已隐藏 {hiddenCount} 项已添加素材</span>}
        </div>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          <strong aria-live="polite">本次新增 {toAdd.length} 项</strong>
          <Button variant="secondary" isDisabled={pending || targetRead.loading} onPress={close}>取消</Button>
          <Button isDisabled={!toAdd.length || disabled} isPending={pending || targetRead.loading} onPress={() => onAdd(toAdd)}>确认添加（{toAdd.length}）</Button>
        </div>
      </div>
      <ReadStatus title="目标档案" loading={targetRead.loading} error={targetRead.error} onRetry={targetRead.retry} />
      {hideAssociated && <ReadStatus title="已添加素材数量" loading={associated.loading} error={associated.error} onRetry={associated.retry} />}
      <ActionError message={error ? '提交未完成，待添加的勾选已保留。' + error : ''} />
      {toAdd.length > 0 && <div className="flex flex-wrap gap-2" role="group" aria-label="本次勾选素材">
        {toAdd.map((asset) => <Button key={asset.assetId} size="sm" variant="ghost" className="media-back" isDisabled={disabled}
          onPress={() => toggle(asset, false)}>取消勾选 {asset.filename}</Button>)}
      </div>}
      <ReadStatus title="可选素材" loading={read.loading} error={read.error} onRetry={read.retry} />
      {!read.loading && !read.error && !rows.length && <p className="media-box">{hideAssociated && hasHidden
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
      <MediaPagination name="可选素材分页" offset={offset} limit={mediaPageSize} total={read.data?.total ?? 0}
        hasNext={offset + mediaPageSize < Number(read.data?.total ?? 0)} loading={disabled || read.loading}
        onChange={(value) => change({ offset: value })} />
    </RecordPage>
    <DiscardDialog open={discard} description="本次勾选的素材尚未添加，返回档案会清除这些选择。"
      onKeep={() => setDiscard(false)} onDiscard={onClose} />
  </>;
}
