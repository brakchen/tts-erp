import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Chip, Label } from '@heroui/react';
import type { FrontendBootstrap } from '../../shared/contracts';
import AssetCard from '../media-assets/AssetCard';
import { CursorTail, useCursorFeed } from '../media-assets/CursorFeed';
import OperationConfirmation from '../media-assets/OperationConfirmation';
import ViewAsset from '../media-assets/ViewAsset';
import { decodeAssetList, decodeDigitalHuman, type DigitalHuman } from '../media-assets/contracts';
import { AssetTags, ReadStatus, assetType, mediaWrite, useMediaAction, useMediaRead } from '../media-assets/media-ui';

export default function HumanMaterials({ bootstrap, id, revision, canWrite, onBack, onPick, onChanged }: {
  bootstrap: FrontendBootstrap; id: string; revision: number; canWrite: boolean;
  onBack: () => void; onPick: (human: DigitalHuman) => void; onChanged: () => void;
}) {
  const root = bootstrap.rootPath;
  const read = useMediaRead(root, '/digital-humans/' + id + '?includeAssets=false', decodeDigitalHuman, revision);
  const human = read.data;
  const feed = useCursorFeed(root, human ? '/assets?digitalHumanId=' + id : null, decodeAssetList, revision);
  const [selection, setSelection] = useState<string[]>([]);
  const [view, setView] = useState<string>();
  const [removing, setRemoving] = useState<{ ids: string[]; title: string }>();
  const [notice, setNotice] = useState('');
  const action = useMediaAction();
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, []);
  const activeIds = new Set(human?.assetIds);
  const picked = new Set(selection.filter((assetId) => activeIds.has(assetId)));
  const all = picked.size > 0 && picked.size === activeIds.size;
  const rows = feed.data?.items ?? [];
  function ask(ids: string[], title: string) {
    action.clearError();
    setRemoving({ ids, title });
  }
  function remove() {
    if (!removing) return;
    const path = '/digital-humans/' + id + '/assets/';
    void action.run(async () => {
      if (removing.ids.length === 1) await mediaWrite(root, path + removing.ids[0], 'DELETE', () => undefined);
      else await mediaWrite(root, path + 'batch-remove', 'POST', () => undefined, { assetIds: removing.ids });
      const ids = new Set(removing.ids);
      setSelection((previous) => previous.filter((assetId) => !ids.has(assetId)));
      setNotice('已移除 ' + ids.size + ' 项关联。');
      setRemoving(undefined);
      onChanged();
    });
  }
  return <section className="media-record">
    <header className="media-title">
      <div className="media-record-navigation">
        <Button variant="ghost" className="media-back" onPress={onBack}>← 返回</Button>
        <nav aria-label="面包屑" className="media-caption">
          <ol className="media-actions">
            <li><Button variant="ghost" size="sm" className="media-back" onPress={onBack}>数字人</Button></li>
            {human && <><li aria-hidden="true">/</li><li><Button variant="ghost" size="sm" className="media-back" onPress={onBack}>{human.name}</Button></li></>}
            <li aria-hidden="true">/</li><li aria-current="page"><Button variant="ghost" size="sm"
              onPress={() => heading.current?.focus()}>素材管理</Button></li>
          </ol>
        </nav>
      </div>
      <h1 tabIndex={-1} ref={heading}>{human ? human.name + '的素材' : '素材管理'}</h1>
    </header>
    <ReadStatus title="数字人档案" loading={read.loading} error={read.error} onRetry={read.retry} />
    {notice && <p className="media-status" role="status">{notice}</p>}
    {human && <>
      <div className="media-list-toolbar">
        <h2>关联素材 <span className="text-muted">· {activeIds.size} 项</span></h2>
        <div className="media-actions">
          {canWrite && activeIds.size > 0 && <>
            <Checkbox isSelected={all} isIndeterminate={!all && picked.size > 0}
              onChange={(checked) => setSelection(checked ? [...activeIds] : [])}>
              <Checkbox.Content><Checkbox.Control><Checkbox.Indicator /></Checkbox.Control><Label>全选</Label></Checkbox.Content>
            </Checkbox>
            <span role="status">已选 {picked.size} 项</span>
            <Button variant="danger" isDisabled={!picked.size}
              onPress={() => ask([...picked], '移除 ' + picked.size + ' 项关联素材')}>批量移除</Button>
          </>}
          {canWrite && human.status === 'active' && <Button onPress={() => onPick(human)}>＋ 添加素材</Button>}
        </div>
      </div>
      <ReadStatus title="关联素材" loading={feed.loading} error={!feed.data ? feed.error : undefined} onRetry={feed.retry} />
      {feed.data && !rows.length && <p className="media-box">当前没有关联素材。</p>}
      <ul className="media-grid media-linked-grid" data-media-grid aria-label="关联素材">
        {rows.map((asset) => <li key={asset.assetId} className="media-linked-card">
          <AssetCard asset={asset} root={root}>
            {canWrite && <Checkbox className="media-linked-check" aria-label={'选择 ' + asset.filename} isSelected={picked.has(asset.assetId)}
              onChange={(checked) => setSelection((current) => checked ? [...new Set([...current, asset.assetId])] : current.filter((item) => item !== asset.assetId))}>
              <Checkbox.Content><Checkbox.Control><Checkbox.Indicator /></Checkbox.Control></Checkbox.Content>
            </Checkbox>}
            <div className="media-card-title">
              <h3 title={asset.filename}>{asset.filename}</h3>
              <Chip size="sm" className="media-kind"><Chip.Label>{assetType(asset)}</Chip.Label></Chip>
            </div>
            <AssetTags tags={asset.tags} />
            <div className="media-card-actions">
              <Button variant="ghost" aria-label={'查看素材 ' + asset.filename} onPress={() => setView(asset.assetId)}>查看</Button>
              {canWrite && <Button variant="ghost" className="text-danger" aria-label={'移除关联 ' + asset.filename}
                onPress={() => ask([asset.assetId], '移除「' + asset.filename + '」的关联')}>移除关联</Button>}
            </div>
          </AssetCard>
        </li>)}
      </ul>
      <CursorTail {...feed} paused={Boolean(view || removing)} />
    </>}
    {view && <ViewAsset bootstrap={bootstrap} id={view} onClose={() => setView(undefined)} />}
    {removing && <OperationConfirmation title={removing.title}
      description="仅解除与当前档案的关联，素材和其他档案的关联会保留。"
      pending={action.pending} error={action.error} confirmLabel="确认移除"
      onConfirm={remove} onCancel={() => setRemoving(undefined)} />}
  </section>;
}
