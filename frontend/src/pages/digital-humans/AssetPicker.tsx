import { useState } from 'react';
import { Button, Checkbox, Input, Label, TextField } from '@heroui/react';
import AssetCard from '../media-assets/AssetCard';
import { assetKinds, decodeAssetList, type Asset, type DigitalHumanDetail } from '../media-assets/contracts';
import { ActionError, DiscardDialog, MediaPagination, MediaSelect, ReadStatus, RecordPage, useLeaveGuard, useMediaRead } from '../media-assets/media-ui';

const limit = 12;
export interface PickerDraft {
  keyword: string; query: string; kind: string; offset: number; hideAssociated: boolean; selected: Asset[];
}
export const emptyPickerDraft: PickerDraft = { keyword: '', query: '', kind: '', offset: 0, hideAssociated: true, selected: [] };

export default function AssetPicker({ root, human, pending, error, notice, onAdd, onClose, onUpload, draft, onDraft, targetRead }: {
  root: string; human: DigitalHumanDetail; pending: boolean; error: string; notice: string;
  onAdd: (assets: readonly Asset[]) => void; onClose: () => void; onUpload: () => void;
  draft: PickerDraft; onDraft: (draft: PickerDraft) => void;
  targetRead: { loading: boolean; error?: string; retry: () => void };
}) {
  const [discard, setDiscard] = useState(false);
  const { keyword, query, kind, offset, hideAssociated, selected } = draft;
  const toAdd = selected.filter((asset) => !human.assetIds.includes(asset.assetId));
  const disabled = pending || targetRead.loading || Boolean(targetRead.error);
  useLeaveGuard(Boolean(toAdd.length) || pending);
  function change(values: Partial<PickerDraft>) { onDraft({ ...draft, ...values }); }
  function toggle(asset: Asset, checked: boolean) {
    change({ selected: checked ? [...selected.filter((item) => item.assetId !== asset.assetId), asset] : selected.filter((item) => item.assetId !== asset.assetId) });
  }
  function close() { if (toAdd.length) setDiscard(true); else onClose(); }
  const params = new URLSearchParams({ status: 'ready', limit: String(limit) });
  if (query) params.set('keyword', query);
  if (kind) params.set('kind', kind);
  if (offset) params.set('offset', String(offset));
  if (hideAssociated) params.set('excludeDigitalHumanId', human.digitalHumanId);
  // The server excludes associations before pagination. Current draft selections remain visible.
  const read = useMediaRead(root, '/assets?' + params, decodeAssetList, human.assetIds.length);
  const rows = read.data?.items ?? [];
  return <>
    <RecordPage title={'选择已有素材 · ' + human.name} target={human.name} pending={pending || targetRead.loading} onClose={close} backLabel="返回档案">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p>勾选后统一添加。搜索、切换类型和翻页都会保留本次选择。</p>
        <Button variant="secondary" isDisabled={disabled} onPress={onUpload}>上传新素材</Button>
      </div>
      <form className="erp-filter-row" onSubmit={(event) => { event.preventDefault(); change({ query: keyword.trim(), offset: 0 }); }}>
        <TextField value={keyword} onChange={(value) => change({ keyword: value })} isDisabled={disabled}>
          <Label>搜索素材</Label><Input placeholder="输入名称、说明或标签" maxLength={120} />
        </TextField>
        <MediaSelect label="素材类型" value={kind} options={assetKinds} disabled={disabled}
          onChange={(value) => change({ kind: value, offset: 0 })} />
        <Button type="submit" isDisabled={disabled}>搜索</Button>
      </form>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Checkbox isSelected={hideAssociated} isDisabled={disabled}
          onChange={(checked) => change({ hideAssociated: checked, offset: 0 })}>
          <Checkbox.Content><Checkbox.Control><Checkbox.Indicator /></Checkbox.Control><Label>隐藏已选内容</Label></Checkbox.Content>
        </Checkbox>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          <span role="status">本次新增 {toAdd.length} 项</span>
          <Button variant="secondary" isDisabled={pending || targetRead.loading} onPress={close}>取消</Button>
          <Button isDisabled={!toAdd.length || disabled} isPending={pending || targetRead.loading} onPress={() => onAdd(toAdd)}>确认添加({toAdd.length})</Button>
        </div>
      </div>
      <p className="text-sm text-muted">隐藏的是此数字人已关联的素材。本次新勾选的素材会继续显示，确认前不会写入关联。</p>
      <ReadStatus title="目标档案" loading={targetRead.loading} error={targetRead.error} onRetry={targetRead.retry} />
      {notice && <p role="status" className="[overflow-wrap:anywhere]">{notice}</p>}
      <ActionError message={error ? '提交未完成，待添加的勾选已保留。' + error : ''} />
      {toAdd.length > 0 && <details className="rounded-lg border border-default p-3">
        <summary className="cursor-pointer">已勾选 {toAdd.length} 项 · 查看或取消勾选</summary>
        <ul className="mt-2 space-y-1">
          {toAdd.map((asset) => <li className="flex flex-wrap items-center gap-2" key={asset.assetId}>
            <span className="min-w-0 [overflow-wrap:anywhere]">{asset.filename}</span>
            <Button size="sm" variant="secondary" isDisabled={disabled} aria-label={'取消勾选 ' + asset.filename}
              onPress={() => toggle(asset, false)}>取消勾选</Button>
          </li>)}
        </ul>
      </details>}
      <ReadStatus title="可选素材" loading={read.loading} error={read.error} onRetry={read.retry} />
      {!read.loading && !read.error && !rows.length && <p>{query || kind ? '没有匹配的已入库素材。请调整搜索条件。'
        : hideAssociated ? '没有尚未添加的已入库素材。可以上传新素材，或取消隐藏查看已有引用。' : '还没有已入库素材，可以直接上传新素材。'}</p>}
      {read.data && !read.loading && <p className="text-sm text-muted">共 {read.data.total} 项可选素材</p>}
      <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
        {rows.map((asset) => {
          const associated = human.assetIds.includes(asset.assetId);
          return <li key={asset.assetId} className="min-w-0"><AssetCard asset={asset} root={root} title={
            <Checkbox isSelected={associated || toAdd.some((item) => item.assetId === asset.assetId)}
              isDisabled={associated || disabled || read.loading || asset.status !== 'ready'}
              onChange={(checked) => toggle(asset, checked)}>
              <Checkbox.Content><Checkbox.Control><Checkbox.Indicator /></Checkbox.Control>
                <Label className="min-w-0 font-semibold [overflow-wrap:anywhere]">{asset.filename}</Label></Checkbox.Content>
            </Checkbox>}>
            {associated && <p className="[overflow-wrap:anywhere]">已添加至「{human.name}」</p>}
          </AssetCard></li>;
        })}
      </ul>
      <MediaPagination name="可选素材分页" offset={offset} limit={limit} hasNext={offset + limit < Number(read.data?.total ?? 0)}
        loading={disabled || read.loading} onChange={(value) => change({ offset: value })} />
    </RecordPage>
    <DiscardDialog open={discard} description="本次勾选的素材尚未添加，返回档案会清除这些选择。"
      onKeep={() => setDiscard(false)} onDiscard={onClose} />
  </>;
}
