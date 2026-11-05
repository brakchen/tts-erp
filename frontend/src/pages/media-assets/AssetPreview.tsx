import { useState } from 'react';
import { Button } from '@heroui/react';
import { decodeAsset, type Asset } from './contracts';
import { mediaRequest, useMediaAction } from './media-ui';

export default function AssetPreview({ asset, root, compact = false }: { asset: Asset; root: string; compact?: boolean }) {
  const [replacement, setReplacement] = useState<Asset>();
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const action = useMediaAction();
  const source = replacement ?? asset;
  const url = source.url;
  const unavailable = !url || failedUrl === url;
  const retry = () => void action.run(async () => {
    const refreshed = await mediaRequest(root, '/assets/' + asset.assetId, decodeAsset);
    if (refreshed.assetId !== asset.assetId) throw new Error('预览对象不符');
    setReplacement(refreshed);
    setFailedUrl(null);
  });
  if (unavailable) return <div className="space-y-2 rounded-lg border border-default p-3">
    <p role="status">{source.status === 'ready' ? '暂时无法预览此文件。' : '入库完成后可以预览文件。'}</p>
    {source.status === 'ready' && <Button variant="secondary" isPending={action.pending} onPress={retry}>重新获取预览</Button>}
    {action.error && <p role="alert">{action.error}</p>}
  </div>;
  return <div className={'flex min-w-0 items-center justify-center rounded-lg bg-default/40 p-2 ' + (compact ? 'aspect-video' : '')}>
    {source.kind === 'video'
      ? <video key={url} src={url} aria-label={'视频预览 ' + source.filename} controls preload="metadata" playsInline
          className={compact ? 'h-full max-w-full object-contain' : 'max-h-96 max-w-full'} onError={() => setFailedUrl(url)} />
      : <img key={url} src={url} alt={'图片预览 ' + source.filename} className={compact ? 'h-full max-w-full object-contain' : 'max-h-96 max-w-full object-contain'}
          onError={() => setFailedUrl(url)} />}
  </div>;
}
