import { useState } from 'react';
import { Button, Checkbox, Input, Label, TextField } from '@heroui/react';
import AssetPreview from '../media-assets/AssetPreview';
import { assetKinds, decodeAssetList, type Asset, type DigitalHumanDetail } from '../media-assets/contracts';
import { ActionError, AssetFacts, MediaPagination, MediaSelect, ReadStatus, RecordDialog, useMediaRead } from '../media-assets/media-ui';

const limit = 12;

export default function AssetPicker({ root, human, pending, error, notice, onAdd, onClose }: {
  root: string; human: DigitalHumanDetail; pending: boolean; error: string; notice: string;
  onAdd: (assets: readonly Asset[]) => void; onClose: () => void;
}) {
  const [keyword, setKeyword] = useState('');
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState('');
  const [offset, setOffset] = useState(0);
  const [preview, setPreview] = useState<string>();
  const [selected, setSelected] = useState<Asset[]>([]);
  const toAdd = selected.filter((asset) => !human.assetIds.includes(asset.assetId));

  function toggle(asset: Asset, checked: boolean) {
    setSelected((previous) => checked ? [...previous, asset] : previous.filter((item) => item.assetId !== asset.assetId));
  }
  const params = new URLSearchParams({ status: 'ready', limit: String(limit) });
  if (query) params.set('keyword', query);
  if (kind) params.set('kind', kind);
  if (offset) params.set('offset', String(offset));
  const read = useMediaRead(root, '/assets?' + params, decodeAssetList);
  const rows = read.data?.items ?? [];

  return <RecordDialog title={'选择已有资产 · ' + human.name} pending={pending} onClose={onClose} footer={
    <div className="flex w-full flex-wrap items-center justify-end gap-2">
      <Button variant="secondary" isDisabled={pending} onPress={onClose}>返回档案</Button>
      <Button isDisabled={toAdd.length === 0} isPending={pending} onPress={() => onAdd(toAdd)}>
        {pending ? '正在添加…' : '添加选中的资产' + (toAdd.length ? '（' + toAdd.length + '）' : '')}
      </Button>
    </div>
  }>
    <p>勾选要引用的素材后统一添加。搜索和翻页会保留已勾选的资产。</p>
    <form className="erp-filter-row" onSubmit={(event) => {
      event.preventDefault(); setQuery(keyword.trim()); setOffset(0); setPreview(undefined);
    }}>
      <TextField value={keyword} onChange={setKeyword} isDisabled={pending}>
        <Label>搜索资产</Label><Input placeholder="输入名称或说明" maxLength={120} />
      </TextField>
      <MediaSelect label="资产类型" value={kind} options={assetKinds} disabled={pending}
        onChange={(value) => { setKind(value); setOffset(0); setPreview(undefined); }} />
      <Button type="submit" isDisabled={pending}>搜索</Button>
    </form>
    {notice && <p role="status" className="[overflow-wrap:anywhere]">{notice}</p>}
    <ActionError message={error ? '提交未完成，待添加的勾选已保留。' + error : ''} />
    {toAdd.length > 0 && <details className="rounded-lg border border-default p-3">
      <summary className="cursor-pointer">已勾选 {toAdd.length} 项 · 查看或取消勾选</summary>
      <ul className="mt-2 space-y-1">
        {toAdd.map((asset) => <li className="flex flex-wrap items-center gap-2" key={asset.assetId}>
          <span className="min-w-0 [overflow-wrap:anywhere]">{asset.filename}</span>
          <Button size="sm" variant="secondary" isDisabled={pending} aria-label={'取消勾选 ' + asset.filename}
            onPress={() => toggle(asset, false)}>取消勾选</Button>
        </li>)}
      </ul>
    </details>}
    <ReadStatus title="可选资产" loading={read.loading} error={read.error} onRetry={read.retry} />
    {!read.loading && !read.error && !rows.length && <p>{query || kind ? '没有匹配的已入库资产。请调整搜索条件。' : '还没有已入库资产。返回档案后可以上传新资产。'}</p>}
    {read.data && !read.loading && <p className="text-sm text-muted">共 {read.data.total} 项就绪资产</p>}
    <ul className="space-y-3">
      {rows.map((asset) => <li key={asset.assetId} className="min-w-0 space-y-2 rounded-lg border border-default p-3">
        {human.assetIds.includes(asset.assetId) ? <>
          <h3 className="break-words font-semibold">{asset.filename}</h3>
          <p>已添加</p>
        </> : <Checkbox isSelected={toAdd.some((item) => item.assetId === asset.assetId)}
          isDisabled={pending || read.loading || asset.status !== 'ready'}
          onChange={(checked) => toggle(asset, checked)}>
          <Checkbox.Content>
            <Checkbox.Control><Checkbox.Indicator /></Checkbox.Control>
            <Label className="min-w-0 font-semibold [overflow-wrap:anywhere]">{asset.filename}</Label>
          </Checkbox.Content>
        </Checkbox>}
        <AssetFacts asset={asset} />
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" aria-expanded={preview === asset.assetId}
            aria-label={(preview === asset.assetId ? '收起预览 ' : '预览 ') + asset.filename}
            onPress={() => setPreview(preview === asset.assetId ? undefined : asset.assetId)}>
            {preview === asset.assetId ? '收起预览' : '预览'}
          </Button>
        </div>
        {preview === asset.assetId && <AssetPreview asset={asset} root={root} />}
      </li>)}
    </ul>
    <MediaPagination name="可选资产分页" offset={offset} limit={limit} hasNext={offset + limit < Number(read.data?.total ?? 0)}
      loading={pending || read.loading} onChange={(value) => { setOffset(value); setPreview(undefined); }} />
  </RecordDialog>;
}
