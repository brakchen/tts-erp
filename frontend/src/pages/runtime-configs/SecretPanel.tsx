import { useEffect, useState } from 'react';
import { Button, FieldError, Input, Label, Modal, Table, TextField } from '@heroui/react';
import type { Secret } from './contracts';
export default function SecretPanel({ secrets, disabled, errors, edited, invalid, save, lifecycle, dirty }: {
  secrets: Secret[]; disabled: boolean; errors: Record<string, string>; edited: () => void; invalid: (message: string) => void;
  save: (name: string, value: string) => Promise<boolean>; lifecycle: (secret: Secret) => void; dirty: (value: boolean) => void;
}) {
  const [form, setForm] = useState<'create' | 'update'>(); const [name, setName] = useState(''); const [value, setValue] = useState(''); const [confirm, setConfirm] = useState(false);
  useEffect(() => { dirty(Boolean(form && (value || (form === 'create' && name)))); }, [form, value, name, dirty]);
  function start(mode: 'create' | 'update', target = '') {
    if (value || (form === 'create' && name)) { invalid('当前凭证输入尚未保存，请先保存或明确放弃输入。'); return; }
    edited(); setForm(mode); setName(target); setValue('');
  }
  async function persist() {
    setConfirm(false); if (disabled) return;
    if (await save(name.trim(), value)) { setValue(''); setName(''); setForm(undefined); }
  }
  async function submit() {
    if (disabled) return;
    if (!/^[a-z0-9_.-]{1,128}$/.test(name.trim()) || !value.length || value.length > 16384) { invalid('请填写有效凭证名称和 1–16384 字符的明文'); return; }
    if (form === 'create' && secrets.some((s) => s.name === name.trim())) { invalid('名称已存在，请从列表选择更新或恢复该凭证。'); return; }
    if (form === 'update') setConfirm(true); else await persist();
  }
  return <section className="space-y-4" aria-label="凭证管理"><h2 className="text-xl font-semibold">凭证管理</h2><p>明文永不回读；配置中使用 secret:// 引用。凭证更新独立生效，不需要重新发布引用它的配置。</p>
    <Button variant="secondary" isDisabled={disabled} onPress={() => start('create')}>新增凭证</Button>
    {form && <form aria-label="凭证写入" className="space-y-3" onSubmit={(e) => { e.preventDefault(); void submit(); }}><h3 className="font-semibold">{form === 'update' ? '更新凭证' : '新增凭证'}</h3>
      <TextField value={name} isDisabled={disabled || form === 'update'} isRequired onChange={(v) => { edited(); setName(v); }}><Label>凭证名称</Label><Input maxLength={128} autoComplete="off" /></TextField>
      <TextField value={value} isDisabled={disabled} isRequired isInvalid={Boolean(errors.value)} onChange={(v) => { edited(); setValue(v); }}><Label>凭证明文</Label><Input type="password" maxLength={16384} autoComplete="new-password" /><FieldError>{errors.value}</FieldError></TextField>
      <div className="flex flex-wrap gap-3"><Button type="submit" isDisabled={disabled}>{form === 'update' ? '更新并加密保存' : '加密保存新凭证'}</Button><Button variant="secondary" isDisabled={disabled} onPress={() => { setValue(''); setName(''); setForm(undefined); }}>放弃输入</Button></div>
    </form>}
    <Table><Table.ScrollContainer role="region" aria-label="凭证元数据表格"><Table.Content aria-label="凭证元数据" className="min-w-[42rem]"><Table.Header><Table.Column isRowHeader>引用</Table.Column><Table.Column>指纹</Table.Column><Table.Column>更新时间</Table.Column><Table.Column>状态</Table.Column><Table.Column>操作</Table.Column></Table.Header><Table.Body renderEmptyState={() => '暂无凭证'}>{secrets.map((secret) => <Table.Row key={secret.name} id={secret.name}><Table.Cell><code className="block break-all">{secret.ref}</code></Table.Cell><Table.Cell>{secret.fingerprint}</Table.Cell><Table.Cell>{secret.updatedAt}</Table.Cell><Table.Cell>{secret.retiredAt ? '已归档' : '有效'}</Table.Cell><Table.Cell focusMode="child"><div className="flex flex-wrap gap-2">{!secret.retiredAt && <Button size="sm" variant="secondary" isDisabled={disabled} onPress={() => start('update', secret.name)}>更新凭证 {secret.name}</Button>}<Button size="sm" variant="secondary" isDisabled={disabled} onPress={() => lifecycle(secret)}>{secret.retiredAt ? '恢复凭证' : '归档凭证'} {secret.name}</Button></div></Table.Cell></Table.Row>)}</Table.Body></Table.Content></Table.ScrollContainer></Table>
    <Modal.Backdrop isOpen={confirm} onOpenChange={setConfirm}><Modal.Container><Modal.Dialog><Modal.CloseTrigger /><Modal.Header><Modal.Heading>确认更新凭证</Modal.Heading></Modal.Header><Modal.Body className="space-y-3"><p className="break-all">{name}</p><p>更新后，引用它的业务在后续读取时可能使用新值，不需要重新发布配置。历史配置也不会恢复旧凭证明文。</p><Button isDisabled={disabled} onPress={() => { void persist(); }}>确认更新凭证</Button><Button variant="secondary" onPress={() => setConfirm(false)}>取消</Button></Modal.Body></Modal.Dialog></Modal.Container></Modal.Backdrop>
  </section>;
}
