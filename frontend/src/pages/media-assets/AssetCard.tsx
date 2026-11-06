import type { ReactNode } from 'react';
import { Card } from '@heroui/react';
import AssetPreview from './AssetPreview';
import type { Asset } from './contracts';

export default function AssetCard({ asset, root, children }: {
  asset: Asset; root: string; children: ReactNode;
}) {
  return <Card className="media-box media-card shadow-none">
    <Card.Content className="media-card-content">
      <AssetPreview asset={asset} root={root} compact />
      {children}
    </Card.Content>
  </Card>;
}
