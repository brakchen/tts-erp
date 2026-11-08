import { Chip } from '@heroui/react';
import type { FrontendBootstrap } from '../../shared/contracts';
import type { Asset } from './contracts';
import { humanStatus, recordLink } from './media-ui';

export default function AssetReferences({ bootstrap, references, packs }: {
  bootstrap: FrontendBootstrap; references: Asset['usedBy']; packs?: Asset['usedByPacks'];
}) {
  const empty = references ? '还没有数字人引用这份素材。' : '当前无法确认引用情况。';
  return <><section className="media-stack" aria-label="哪些数字人在用">
    <h3 className="font-semibold">哪些数字人在用</h3>
    {references?.length ? <ul className="grid min-w-0 gap-2">
      {references.map((human) => {
        const href = recordLink(bootstrap, 'digital-humans', 'human', human.id);
        return <li key={human.id} className="flex min-w-0 flex-wrap items-center justify-between gap-2">
          {href ? <a className="underline underline-offset-2 [overflow-wrap:anywhere]" href={href}>{human.name}</a>
            : <span className="[overflow-wrap:anywhere]">{human.name}</span>}
          <Chip size="sm" className="media-chip"><Chip.Label>{humanStatus(human.status)}</Chip.Label></Chip>
        </li>;
      })}
    </ul> : <p className="text-muted">{empty}</p>}
  </section>
  {Boolean(packs?.length) && <section className="media-stack" aria-label="所在素材包">
    <h3 className="font-semibold">所在素材包</h3>
    <ul className="grid min-w-0 gap-2">{packs?.map((pack) => {
      const href = recordLink(bootstrap, 'media-assets', 'pack', pack.packId);
      return <li key={pack.packId} className="[overflow-wrap:anywhere]">{href
        ? <a className="underline underline-offset-2" href={href}>{pack.name}</a> : pack.name}</li>;
    })}</ul>
  </section>}</>;
}
