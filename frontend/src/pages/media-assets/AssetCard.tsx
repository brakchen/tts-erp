import type { ReactNode } from 'react';
import { Card } from '@heroui/react';
import AssetPreview from './AssetPreview';
import type { Asset } from './contracts';
import { AssetFacts } from './media-ui';

export default function AssetCard({ asset, root, title, children }: {
  asset: Asset; root: string; title?: ReactNode; children?: ReactNode;
}) {
  return <Card className="h-full min-w-0">
    <Card.Content><div className="space-y-3 min-w-0">
      <AssetPreview asset={asset} root={root} compact />
      {title ?? <h3 className="font-semibold [overflow-wrap:anywhere]">{asset.filename}</h3>}
      <AssetFacts asset={asset} />
      {children}
    </div></Card.Content>
  </Card>;
}
