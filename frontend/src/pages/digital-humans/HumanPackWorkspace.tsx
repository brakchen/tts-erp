import { useState } from 'react';
import { Button } from '@heroui/react';
import type { FrontendBootstrap } from '../../shared/contracts';
import { PackCatalog, PackDetail } from '../media-assets/PackViews';
import { decodeDigitalHuman } from '../media-assets/contracts';
import { ReadStatus, useMediaRead } from '../media-assets/media-ui';
import { PortraitTile } from './HumanEditor';

export default function HumanPackWorkspace({ bootstrap, id, revision, canWrite, onBack, onLegacy }: {
  bootstrap: FrontendBootstrap;
  id: string;
  revision: number;
  canWrite: boolean;
  onBack: () => void;
  onLegacy: () => void;
}) {
  const [packId, setPackId] = useState<string>();
  const [packRevision, setPackRevision] = useState(0);
  const read = useMediaRead(bootstrap.rootPath, '/digital-humans/' + id + '?includeAssets=false', decodeDigitalHuman, revision);
  const human = read.data;
  if (packId && human) return <PackDetail key={packId} bootstrap={bootstrap} id={packId} canWrite={canWrite}
    backLabel={human.name} onBack={() => { setPackId(undefined); setPackRevision((current) => current + 1); }} />;

  return <section className="media-record">
    <header className="media-title">
      <Button variant="ghost" className="media-back self-start" onPress={onBack}>← 数字人</Button>
      {human && <div className="media-person-identity">
        <PortraitTile url={human.portraitUrl} label={human.name + '的形象照片'} />
        <div className="min-w-0 [overflow-wrap:anywhere]">
          <h1>{human.name}</h1>
          <p className="text-muted">{human.description || '数字人档案'}</p>
        </div>
      </div>}
    </header>
    <ReadStatus title="数字人档案" loading={read.loading} error={read.error} onRetry={read.retry} />
    {human && <>
      <PackCatalog bootstrap={bootstrap} canWrite={canWrite} human={human} revision={revision + packRevision} onOpen={setPackId} />
      {human.assetIds.length > 0 && <Button variant="secondary" className="self-start" onPress={onLegacy}>
        管理原有素材 · {human.assetIds.length}
      </Button>}
    </>}
  </section>;
}
