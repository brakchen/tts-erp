import { useState } from 'react';
import type { FrontendBootstrap } from '../../shared/contracts';
import UploadAsset from '../media-assets/UploadAsset';
import { decodeDigitalHuman, type Asset, type DigitalHuman } from '../media-assets/contracts';
import { mediaWrite, useMediaAction, useMediaRead } from '../media-assets/media-ui';
import AssetPicker, { emptyPickerDraft } from './AssetPicker';

export default function PickAssets({ bootstrap, target, onClose, onChanged }: {
  bootstrap: FrontendBootstrap; target: DigitalHuman;
  onClose: (message?: string) => void; onChanged: () => void;
}) {
  const root = bootstrap.rootPath;
  const path = '/digital-humans/' + target.digitalHumanId;
  const [upload, setUpload] = useState(false);
  const [draft, setDraft] = useState(emptyPickerDraft);
  const [revision, setRevision] = useState(0);
  const [acknowledged, setAcknowledged] = useState<DigitalHuman>();
  const [notice, setNotice] = useState('');
  const action = useMediaAction();
  const read = useMediaRead(root, path + '?includeAssets=false', decodeDigitalHuman, revision);
  const human = acknowledged ?? read.data ?? target;
  function add(assets: readonly Asset[]) {
    if (read.loading || read.error) return;
    void action.run(async () => {
      let current = human;
      let added = 0;
      try {
        for (const asset of assets) {
          if (current.assetIds.includes(asset.assetId)) continue;
          const updated = await mediaWrite(root, path + '/assets', 'POST', decodeDigitalHuman, { assetId: asset.assetId });
          if (updated.digitalHumanId !== target.digitalHumanId || !updated.assetIds.includes(asset.assetId)) throw new Error('添加结果不符');
          current = updated;
          added += 1;
          setAcknowledged(updated);
        }
      } finally {
        if (added) {
          setNotice('已添加 ' + added + ' 项素材。');
          onChanged();
        }
      }
      onClose('已为「' + target.name + '」添加 ' + added + ' 项素材。');
    });
  }
  function uploadChanged(message: string) {
    setUpload(false);
    setNotice(message);
    setAcknowledged(undefined);
    setRevision((value) => value + 1);
    onChanged();
  }
  return <>
    <AssetPicker root={root} human={human} pending={action.pending} error={action.error} notice={notice}
      onAdd={add} onClose={() => onClose(notice)} onUpload={() => setUpload(true)} uploading={upload}
      draft={draft} onDraft={setDraft} targetRead={read} revision={revision} />
    {upload && <UploadAsset bootstrap={bootstrap} target={{ id: target.digitalHumanId, name: target.name }}
      onClose={(completed) => { if (completed) uploadChanged('素材已入库，可在列表中勾选添加。'); else setUpload(false); }}
      onComplete={(_id, filename) => uploadChanged('已入库并添加「' + filename + '」。')} />}
  </>;
}
