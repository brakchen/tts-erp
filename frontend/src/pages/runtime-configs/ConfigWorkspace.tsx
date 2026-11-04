import { Button, Disclosure } from '@heroui/react';
import type { Detail } from './contracts';
import JsonEditor from './JsonEditor';
import { stringifyRuntime } from './json';
export default function ConfigWorkspace({ detail, editing, text, disabled, dirty, latest, onText, edit, save, check, history, lifecycle, loadLatest, rebase, conflict }: {
  detail: Detail; editing: boolean; text: string; disabled: boolean; dirty: boolean; latest?: Detail;
  onText: (value: string) => void; edit: () => void; save: () => void; check: () => void; history: () => void; lifecycle: () => void;
  loadLatest: () => void; rebase: () => void; conflict: boolean;
}) {
  return <section className="erp-panel space-y-4 min-w-0" aria-label={editing ? '修改配置' : '配置概览'}>
    <p>{detail.retiredAt ? '已停用，不再出现在运行快照。' : detail.publishedVersion === null ? '尚未发布' : `当前已发布 v${detail.publishedVersion}`}</p>
    {editing ? <>
      <p role="status">{dirty ? '你的修改尚未保存，也未发布。' : detail.hasDraft ? '当前正在编辑已保存、尚未发布的草稿。' : '你正在修改配置，确认发布后才会生效。'}</p>
      <JsonEditor key="working-copy" label="配置内容" value={text} disabled={disabled} onChange={onText} />
      {conflict && <div className="space-y-2"><p>先核对服务端当前内容，再决定是否使用自己的修改。</p><Button variant="secondary" isDisabled={disabled} onPress={loadLatest}>查看最新内容</Button></div>}
      {latest && <section role="region" aria-label="最新服务端内容" className="space-y-2"><h3 className="font-semibold">最新服务端内容</h3><p>已发布 {latest.publishedVersion === null ? '无' : `v${latest.publishedVersion}`}；草稿版本 {latest.draftVersion}</p><pre className="whitespace-pre-wrap break-all">{stringifyRuntime(latest.draftPayload ?? latest.publishedPayload ?? {}, 2)}</pre><Button variant="secondary" isDisabled={disabled} onPress={rebase}>保留我的内容，按最新版本重新检查</Button></section>}
      <div className="flex flex-wrap gap-3"><Button variant="secondary" isDisabled={disabled || conflict} onPress={save}>暂存草稿</Button><Button isDisabled={disabled || conflict} onPress={check}>检查并发布</Button></div>
    </> : <>
      {detail.hasDraft && <p>另有一份已保存、尚未发布的草稿；下方展示当前生效内容。</p>}
      {detail.publishedPayload === null ? <p>业务暂时无法通过运行快照读取此配置。</p> : <JsonEditor key="published-content" label="当前生效内容" readOnly value={stringifyRuntime(detail.publishedPayload, 2)} />}
      <div className="flex flex-wrap gap-3">{!detail.retiredAt && <Button isDisabled={disabled} onPress={edit}>{detail.publishedVersion === null ? '完成配置并发布' : detail.hasDraft ? '继续编辑草稿' : '修改配置'}</Button>}<Button variant="secondary" isDisabled={disabled} onPress={history}>查看发布历史</Button>
        <Disclosure><Disclosure.Heading><Disclosure.Trigger type="button">更多操作<Disclosure.Indicator /></Disclosure.Trigger></Disclosure.Heading><Disclosure.Content><Disclosure.Body><Button variant="secondary" isDisabled={disabled} onPress={lifecycle}>{detail.retiredAt ? '重新启用配置' : '停用配置'}</Button></Disclosure.Body></Disclosure.Content></Disclosure>
      </div>
    </>}
    <Disclosure><Disclosure.Heading><Disclosure.Trigger type="button">高级信息：配置键与校验规则<Disclosure.Indicator /></Disclosure.Trigger></Disclosure.Heading><Disclosure.Content><Disclosure.Body><code className="block break-all">{detail.configKey}</code><p>校验规则在创建后不可修改。</p><pre className="whitespace-pre-wrap break-all">{stringifyRuntime(detail.jsonSchema, 2)}</pre></Disclosure.Body></Disclosure.Content></Disclosure>
  </section>;
}
