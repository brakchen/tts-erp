import { useState } from 'react';
import { Button, Input, Label, TextField } from '@heroui/react';
import AssetPreview from '../media-assets/AssetPreview';
import { assetKinds, decodeAssetList, type Asset, type DigitalHumanDetail } from '../media-assets/contracts';
import { ActionError, AssetFacts, MediaPagination, MediaSelect, ReadStatus, RecordDialog, useMediaRead } from '../media-assets/media-ui';

const limit = 12;

export default function AssetPicker({ root, human, pending, error, notice, onAdd, onClose }: {
  root: string; human: DigitalHumanDetail; pending: boolean; error: string; notice: string;
  onAdd: (asset: Asset) => void; onClose: () => void;
}) {
  const [keyword, setKeyword] = useState('');
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState('');
  const [offset, setOffset] = useState(0);
  const [preview, setPreview] = useState<string>();
  const params = new URLSearchParams({ status: 'ready', limit: String(limit) });
  if (query) params.set('keyword', query);
  if (kind) params.set('kind', kind);
  if (offset) params.set('offset', String(offset));
  const read = useMediaRead(root, '/assets?' + params, decodeAssetList);
  const rows = read.data?.items ?? [];

  return <RecordDialog title={'选择已有资产 · ' + human.name} pending={pending} onClose={onClose}>
    <p>从全局资产库添加共享引用。已经添加的资产会标明状态。</p>
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
    {notice && <p role="status">{notice}</p>}
    <ActionError message={error ? '添加未完成。' + error : ''} />
    <ReadStatus title="可选资产" loading={read.loading} error={read.error} onRetry={read.retry} />
    {!read.loading && !read.error && !rows.length && <p>{query || kind ? '没有匹配的已入库资产。请调整搜索条件。' : '还没有已入库资产。返回档案后可以上传新资产。'}</p>}
    <ul className="space-y-3">
      {rows.map((asset) => <li key={asset.assetId} className="min-w-0 space-y-2 rounded-lg border border-default p-3">
        <h3 className="break-words font-semibold">{asset.filename}</h3>
        <AssetFacts asset={asset} />
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" aria-expanded={preview === asset.assetId}
            aria-label={(preview === asset.assetId ? '收起预览 ' : '预览 ') + asset.filename}
            onPress={() => setPreview(preview === asset.assetId ? undefined : asset.assetId)}>
            {preview === asset.assetId ? '收起预览' : '预览'}
          </Button>
          {human.assetIds.includes(asset.assetId) ? <span>已添加</span>
            : <Button isDisabled={pending || read.loading || asset.status !== 'ready'} aria-label={'添加 ' + asset.filename} onPress={() => onAdd(asset)}>添加</Button>}
        </div>
        {preview === asset.assetId && <AssetPreview asset={asset} root={root} />}
      </li>)}
    </ul>
    <MediaPagination name="可选资产分页" offset={offset} limit={limit} hasNext={offset + limit < Number(read.data?.total ?? 0)}
      loading={pending || read.loading} onChange={(value) => { setOffset(value); setPreview(undefined); }} />
    <Button variant="secondary" isDisabled={pending} onPress={onClose}>返回档案</Button>
  </RecordDialog>;
}
