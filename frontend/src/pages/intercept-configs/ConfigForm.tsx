import { useState } from 'react';
import { Button, Checkbox, FieldError, Input, Label, Radio, RadioGroup, TextArea, TextField } from '@heroui/react';
import { blankConfig, labels, type ConfigInput } from './contracts';

export interface ConfigDraft { input: ConfigInput; tagsText: string; tagsEdited: boolean }
export default function ConfigForm({ initial, saved, pending, blocked, errors, retry, onSubmit, onEdit, onCancel }: {
  initial?: ConfigInput; saved?: ConfigDraft; pending: boolean; blocked: boolean; errors: Record<string, string>; retry: boolean;
  onSubmit: (value: ConfigInput) => void; onEdit: (draft: ConfigDraft) => void; onCancel: () => void;
}) {
  const [draft, setDraft] = useState<ConfigDraft>(saved ?? { input: initial ?? blankConfig, tagsText: initial?.tags.join(', ') ?? '', tagsEdited: false });
  function change<K extends keyof ConfigInput>(key: K, value: ConfigInput[K]) { const next = { ...draft, input: { ...draft.input, [key]: value } }; onEdit(next); setDraft(next); }
  return <form aria-label="配置表单" className="space-y-3" onSubmit={(event) => { event.preventDefault(); if (!blocked) onSubmit({ ...draft.input, domain: draft.input.domain.trim(), endpoint: draft.input.endpoint.trim(), tags: draft.tagsEdited ? draft.tagsText.split(',').map((tag) => tag.trim()).filter(Boolean) : draft.input.tags }); }}>
    {(['domain', 'endpoint'] as const).map((key) => <TextField key={key} value={draft.input[key]} isRequired isDisabled={pending} isInvalid={Boolean(errors[key])} onChange={(value) => change(key, value)}>
      <Label>{labels[key]}</Label><Input autoComplete="off" /><FieldError>{errors[key]}</FieldError>
    </TextField>)}
    <p>输入完整域名；Endpoint 支持 * 通配符，匹配规则以服务端为准。</p>
    <RadioGroup name="mode" value={draft.input.mode} isDisabled={pending} onChange={(mode) => { if (mode === 'whitelist' || mode === 'blacklist') change('mode', mode); }}><Label>模式</Label>{(['whitelist', 'blacklist'] as const).map((mode) => <Radio key={mode} value={mode}><Radio.Content><Radio.Control><Radio.Indicator /></Radio.Control><Label>{mode === 'whitelist' ? '白名单（记录匹配请求）' : '黑名单（完全跳过）'}</Label></Radio.Content></Radio>)}</RadioGroup>
    <TextField value={draft.input.description ?? ''} isDisabled={pending} isInvalid={Boolean(errors.description)} onChange={(value) => change('description', value)}><Label>描述</Label><TextArea /><FieldError>{errors.description}</FieldError></TextField>
    <p>未填写的描述保持 null；编辑后清空则保存为空字符串。</p>
    <TextField value={draft.tagsText} isDisabled={pending} isInvalid={Boolean(errors.tags)} onChange={(value) => { const next = { ...draft, tagsText: value, tagsEdited: true }; onEdit(next); setDraft(next); }}><Label>标签（逗号分隔）</Label><Input /><FieldError>{errors.tags}</FieldError></TextField>
    <fieldset disabled={pending} className="space-y-2"><legend>采集与状态</legend>{(['capture_headers', 'capture_body', 'enabled'] as const).map((key) => <Checkbox key={key} isSelected={draft.input[key]} isDisabled={pending} onChange={(checked) => change(key, checked)}><Checkbox.Content><Checkbox.Control><Checkbox.Indicator /></Checkbox.Control><Label>{labels[key]}</Label></Checkbox.Content></Checkbox>)}</fieldset>
    <div className="flex flex-wrap gap-3"><Button type="submit" isDisabled={blocked}>{retry ? '核对后明确重新提交' : '保存配置'}</Button><Button variant="secondary" isDisabled={pending} onPress={onCancel}>取消</Button></div>
  </form>;
}
