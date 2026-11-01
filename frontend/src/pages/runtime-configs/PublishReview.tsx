import { useMemo } from 'react';
import { Button, Input, Label, TextField } from '@heroui/react';
import { diffLines } from 'diff';
import type { Review } from './contracts';

/** jsdiff 9.0.0, BSD-3-Clause: https://github.com/kpdecker/jsdiff.
 * Display-only text differences. Eligibility is Review.hasChanges from the server.
 */
export default function PublishReview({ review, comment, disabled, changeComment, back, publish }: {
  review: Review; comment: string; disabled: boolean; changeComment: (value: string) => void; back: () => void; publish: () => void;
}) {
  const changes = useMemo(() => diffLines(review.publishedText ?? '', review.candidateText, { timeout: 100, maxEditLength: 5000 }), [review]);
  return <section aria-label="发布检查" className="space-y-4 min-w-0">
    <h2 className="text-xl font-semibold">检查本次修改</h2>
    <p>{review.publishedVersion === null ? '首次发布' : `当前发布 v${review.publishedVersion}`} · 检查没有保存或发布任何内容。</p>
    {!review.hasChanges && <p role="status">与当前发布内容一致，无需重复发布。</p>}
    <section aria-label="内容变更" className="min-w-0">
      <h3 className="font-semibold">本次内容变更</h3>
      {changes ? <pre className="overflow-auto whitespace-pre-wrap break-all rounded-lg bg-default p-3 text-sm" aria-label="增加和删除的内容">{changes.map((change, index) => <span key={index} className={change.added ? 'block text-emerald-800 dark:text-emerald-300' : change.removed ? 'block text-red-800 dark:text-red-300' : 'block'}>{change.added ? '＋ 新增\n' : change.removed ? '− 删除\n' : '  未变\n'}{change.value}</span>)}</pre> : <p>差异过大，未生成高亮；请检查下方完整内容。</p>}
    </section>
    <details><summary>查看当前生效内容和准备发布的完整内容</summary><div className="grid min-w-0 gap-4 lg:grid-cols-2">
      <section className="min-w-0"><h3 className="font-semibold">当前生效内容</h3><pre className="whitespace-pre-wrap break-all">{review.publishedText ?? '尚未发布'}</pre></section>
      <section className="min-w-0"><h3 className="font-semibold">准备发布的内容</h3><pre className="whitespace-pre-wrap break-all">{review.candidateText}</pre></section>
    </div></details>
    <TextField value={comment} isDisabled={disabled} onChange={changeComment}><Label>发布说明</Label><Input maxLength={2000} placeholder="选填：说明本次修改的目的" /></TextField>
    <p>确认后将保存本次内容并发布新版本。后续读取将使用此版本，正在运行的任务不会因此自动重新执行。</p>
    <div className="flex flex-wrap gap-3"><Button variant="secondary" isDisabled={disabled} onPress={back}>返回修改</Button><Button isDisabled={disabled || !review.hasChanges} onPress={publish}>确认发布</Button></div>
  </section>;
}
