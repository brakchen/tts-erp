import { Button, Modal } from '@heroui/react';
import type { FrontendBootstrap } from '../../shared/contracts';
import AssetPreview from './AssetPreview';
import AssetReferences from './AssetReferences';
import { decodeAsset } from './contracts';
import { AssetTags, ReadStatus, assetStatus, assetType, useMediaRead } from './media-ui';

export default function ViewAsset({ bootstrap, id, onClose }: { bootstrap: FrontendBootstrap; id: string; onClose: () => void }) {
  const root = bootstrap.rootPath;
  const read = useMediaRead(root, '/assets/' + id, decodeAsset);
  const asset = read.data;
  return <Modal.Backdrop isOpen onOpenChange={(open) => { if (!open) onClose(); }}>
    <Modal.Container scroll="inside"><Modal.Dialog className="media-dialog">
      <Modal.Header><Modal.Heading>{asset?.filename ?? '查看素材'}</Modal.Heading></Modal.Header>
      <Modal.Body className="media-stack">
        <ReadStatus title="素材详情" loading={read.loading} error={read.error} onRetry={read.retry} />
        {asset && <>
          <AssetPreview asset={asset} root={root} />
          <p>{assetType(asset)}{asset.status !== 'ready' && ' · ' + assetStatus(asset.status)}</p>
          <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{asset.description || '暂无说明'}</p>
          <AssetTags tags={asset.tags} />
          <AssetReferences bootstrap={bootstrap} references={asset.usedBy} packs={asset.usedByPacks} />
        </>}
      </Modal.Body>
      <Modal.Footer><Button variant="secondary" onPress={onClose}>关闭</Button></Modal.Footer>
    </Modal.Dialog></Modal.Container>
  </Modal.Backdrop>;
}
