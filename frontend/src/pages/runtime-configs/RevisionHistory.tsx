import { Button } from '@heroui/react';
import type { Revision } from './contracts';
import { stringifyRuntime } from './json';
export default function RevisionHistory({ rows, disabled, useRevision }: { rows: Revision[]; disabled: boolean; useRevision: (row: Revision) => void }) {
  return <section className="space-y-4" aria-label="发布历史"><h2 className="text-xl font-semibold">发布历史</h2><p>使用历史内容只会带入编辑，检查并确认发布后才会生效。</p>
    {rows.length === 0 && <p>尚无发布历史</p>}
    {rows.map((row) => <article key={row.version} className="space-y-2 border-b border-separator pb-4"><h3 className="font-semibold">v{row.version} · {row.createdAt}</h3><p className="break-all">{row.comment ?? '无发布说明'} · {row.createdBy}</p><details><summary>查看 v{row.version} 内容</summary><pre className="whitespace-pre-wrap break-all">{stringifyRuntime(row.payload, 2)}</pre></details><Button size="sm" variant="secondary" isDisabled={disabled} onPress={() => useRevision(row)}>使用 v{row.version} 内容</Button></article>)}
  </section>;
}
