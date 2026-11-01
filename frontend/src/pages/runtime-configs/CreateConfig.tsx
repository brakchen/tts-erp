import { useEffect, useRef, useState } from 'react';
import { Button, FieldError, Input, Label, TextField } from '@heroui/react';
import JsonEditor from './JsonEditor';
import { parseObject, stringifyRuntime } from './json';
import type { Detail } from './contracts';
export interface CreateDraft { configKey: string; displayName: string; draftPayload: Record<string, unknown>; jsonSchema?: Record<string, unknown> }
export default function CreateConfig({ disabled, errors, edited, invalid, create, preview, dirty }: {
  disabled: boolean; errors: Record<string, string>; edited: () => void; invalid: (message: string) => void;
  create: (draft: CreateDraft) => Promise<Detail | undefined>; preview: (payload: Record<string, unknown>) => Promise<Record<string, unknown> | undefined>; dirty: (value: boolean) => void;
}) {
  const [key, setKey] = useState(''); const [name, setName] = useState(''); const [payload, setPayload] = useState('{}');
  const [schema, setSchema] = useState('{}'); const [custom, setCustom] = useState(false); const [previewing, setPreviewing] = useState(false);
  const [message, setMessage] = useState(''); const flight = useRef(false); const revision = useRef(0);
  useEffect(() => { dirty(Boolean(key || name || payload !== '{}' || custom)); }, [key, name, payload, custom, dirty]);
  function edit() { edited(); revision.current++; setMessage(''); }
  async function infer() {
    if (disabled || flight.current) return;
    let data: Record<string, unknown>; try { data = parseObject(payload); } catch { invalid('初始内容必须是有效 JSON 对象'); return; }
    const current = revision.current; flight.current = true; setPreviewing(true);
    try { const result = await preview(data); if (result && revision.current === current) { setSchema(stringifyRuntime(result, 2)); setMessage('校验规则预览已更新'); } }
    finally { flight.current = false; setPreviewing(false); }
  }
  async function submit() {
    if (disabled || flight.current) return;
    if (!/^[a-z][a-z0-9_.-]{0,127}$/.test(key.trim()) || !name.trim()) { invalid('请填写有效配置键和配置名称'); return; }
    let draft: CreateDraft;
    try { draft = { configKey: key.trim(), displayName: name.trim(), draftPayload: parseObject(payload), ...(custom ? { jsonSchema: parseObject(schema) } : {}) }; }
    catch { invalid('初始内容和校验规则必须是有效 JSON 对象'); return; }
    await create(draft);
  }
  return <section className="space-y-4" aria-label="新建配置"><h2 className="text-xl font-semibold">新建配置</h2><p>创建后尚未发布。新配置需要已有业务接入读取，创建本身不会新增业务功能。</p>
    <form aria-label="创建配置" className="space-y-4" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <TextField value={name} isRequired isDisabled={disabled} isInvalid={Boolean(errors.displayName)} onChange={(v) => { edit(); setName(v); }}><Label>配置名称</Label><Input maxLength={256} /><FieldError>{errors.displayName}</FieldError></TextField>
      <TextField value={key} isRequired isDisabled={disabled} isInvalid={Boolean(errors.configKey)} onChange={(v) => { edit(); setKey(v); }}><Label>配置键</Label><Input maxLength={128} placeholder="例如 example.notifications.v1" /><FieldError>{errors.configKey}</FieldError></TextField>
      <JsonEditor label="初始内容" value={payload} disabled={disabled || previewing} onChange={(v) => { edit(); setPayload(v); }} />
      <details><summary>高级选项：校验规则 Schema</summary><div className="space-y-3"><p>默认根据初始内容自动推断；创建后不可修改。</p><div className="flex flex-wrap gap-3"><Button variant="secondary" isDisabled={disabled || previewing} onPress={() => { void infer(); }}>预览自动校验规则</Button><label><input type="checkbox" checked={custom} disabled={disabled || previewing} onChange={(e) => { edit(); setCustom(e.target.checked); }} /> 自定义校验规则</label></div><JsonEditor label="新配置 Schema" value={schema} disabled={disabled || previewing} readOnly={!custom} onChange={(v) => { edit(); setSchema(v); }} /></div></details>
      {message && <p role="status">{message}</p>}{previewing && <p role="status">正在推断校验规则…</p>}
      {Object.entries(errors).filter(([key]) => ['jsonSchema', 'draftPayload'].includes(key)).map(([key, value]) => <p role="alert" key={key}>{value}</p>)}
      <Button type="submit" isDisabled={disabled || previewing}>创建并继续编辑</Button>
    </form>
  </section>;
}
