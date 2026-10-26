import { useRef, useState } from 'react';
import { Button, FieldError, Input, Label, Modal, TextField } from '@heroui/react';
import JsonEditor from './JsonEditor';
import { parseObject, stringifyRuntime } from './json';
import type { Detail } from './contracts';
export interface CreateDraft { configKey: string; displayName: string; draftPayload: Record<string, unknown>; jsonSchema?: Record<string, unknown> }
export default function CreateConfig({ disabled, pending, replacesUnsavedDraft, errors, edited, invalid, create, preview }: {
  disabled: boolean; pending: boolean; replacesUnsavedDraft: boolean; errors: Record<string, string>; edited: () => void; invalid: (message: string) => void;
  create: (draft: CreateDraft) => Promise<Detail | undefined>;
  preview: (payload: Record<string, unknown>) => Promise<Record<string, unknown> | undefined>;
}) {
  const [key, setKey] = useState(''); const [name, setName] = useState(''); const [payload, setPayload] = useState('{}');
  const [schema, setSchema] = useState('{}'); const [custom, setCustom] = useState(false); const [previewing, setPreviewing] = useState(false);
  const [message, setMessage] = useState(''); const previewFlight = useRef(false); const editVersion = useRef(0);
  const [confirming, setConfirming] = useState<CreateDraft>();
  function edit() { edited(); editVersion.current++; setMessage(''); }
  async function infer() {
    if (disabled || previewFlight.current) return;
    let data: Record<string, unknown>; try { data = parseObject(payload); } catch { invalid('初始草稿必须是有效 JSON 对象'); return; }
    const current = editVersion.current; previewFlight.current = true; setPreviewing(true);
    try { const result = await preview(data); if (result && current === editVersion.current) { setSchema(stringifyRuntime(result, 2)); setCustom(false); setMessage('Schema 预览已更新'); } }
    finally { previewFlight.current = false; setPreviewing(false); }
  }
  async function submit() {
    if (disabled || previewFlight.current) return;
    if (!/^[a-z][a-z0-9_.-]{0,127}$/.test(key.trim()) || !name.trim() || name.trim().length > 256) { invalid('请填写有效配置键和名称'); return; }
    let draft: CreateDraft;
    try { draft = { configKey: key.trim(), displayName: name.trim(), draftPayload: parseObject(payload), ...(custom ? { jsonSchema: parseObject(schema) } : {}) }; }
    catch { invalid('初始草稿和 Schema 必须是有效 JSON 对象'); return; }
    if (replacesUnsavedDraft) { setConfirming(draft); return; }
    await persist(draft);
  }
  async function persist(draft: CreateDraft) {
    if (disabled || previewFlight.current) return;
    try { if (await create(draft)) { setKey(''); setName(''); setPayload('{}'); setSchema('{}'); setCustom(false); setMessage(''); } }
    finally { setConfirming(undefined); }
  }
  return <section className="space-y-3" aria-label="创建配置项"><h2 className="text-lg font-semibold">创建配置项</h2>
    <form aria-label="创建配置" className="space-y-3" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <TextField value={key} isRequired isDisabled={pending} isInvalid={Boolean(errors.configKey)} onChange={(v) => { edit(); setKey(v); }}><Label>新配置键</Label><Input maxLength={128} /><FieldError>{errors.configKey}</FieldError></TextField>
      <TextField value={name} isRequired isDisabled={pending} isInvalid={Boolean(errors.displayName)} onChange={(v) => { edit(); setName(v); }}><Label>新配置名称</Label><Input maxLength={256} /><FieldError>{errors.displayName}</FieldError></TextField>
      <JsonEditor label="初始草稿" value={payload} disabled={pending} onChange={(v) => { edit(); setPayload(v); }} />
      <div className="flex flex-wrap gap-3"><Button type="button" variant="secondary" isDisabled={disabled || previewing} onPress={() => { void infer(); }}>推断 Schema</Button><Button type="button" variant="secondary" isDisabled={pending} onPress={() => { edit(); setCustom(!custom); }}>{custom ? '恢复自动推断' : '自定义 Schema'}</Button></div>
      <p>默认由服务器根据初始草稿推断 Schema；自定义模式才提交 jsonSchema。Schema 创建后不可修改。</p>
      <JsonEditor label="新配置 Schema" value={schema} disabled={pending} readOnly={!custom} onChange={(v) => { edit(); setSchema(v); }} />
      {message && <p role="status">{message}</p>}{previewing && <p role="status">正在推断 Schema…</p>}
      {Object.entries(errors).filter(([key]) => ['jsonSchema', 'draftPayload'].includes(key)).map(([key, value]) => <p role="alert" key={key}>{value}</p>)}
      <Button type="submit" isDisabled={disabled || previewing}>创建草稿</Button>
    </form>
    <Modal.Backdrop isOpen={confirming !== undefined} isDismissable={!pending} isKeyboardDismissDisabled={pending} onOpenChange={(open) => { if (!open && !pending) setConfirming(undefined); }}><Modal.Container size="lg"><Modal.Dialog>
      <Modal.CloseTrigger aria-label="取消创建并保留草稿" isDisabled={pending} /><Modal.Header><Modal.Heading>确认替换未保存草稿</Modal.Heading></Modal.Header>
      <Modal.Body className="space-y-3"><p>当前所选配置有未保存编辑或发布说明。创建成功后将替换所选草稿并放弃这些本地编辑；创建失败则保留。</p><p className="break-all">新配置：{confirming?.configKey}</p><div className="flex flex-wrap gap-3">
        <Button isDisabled={disabled || previewing} onPress={() => { if (confirming) void persist(confirming); }}>确认创建并替换所选草稿</Button>
        <Button variant="secondary" isDisabled={pending} onPress={() => setConfirming(undefined)}>取消</Button>
      </div></Modal.Body>
    </Modal.Dialog></Modal.Container></Modal.Backdrop>
  </section>;
}
