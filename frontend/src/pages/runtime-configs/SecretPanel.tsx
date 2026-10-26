import { useState } from 'react';
import { Button, FieldError, Input, Label, Table, TextField } from '@heroui/react';
import type { Secret } from './contracts';
export default function SecretPanel({ secrets, disabled, pending, errors, edited, invalid, save, lifecycle }: {
  secrets: Secret[]; disabled: boolean; pending: boolean; errors: Record<string, string>; edited: () => void; invalid: (message: string) => void;
  save: (name: string, value: string) => Promise<boolean>; lifecycle: (secret: Secret) => void;
}) {
  const [name, setName] = useState(''); const [value, setValue] = useState('');
  async function submit() {
    if (disabled) return;
    if (!/^[a-z0-9_.-]{1,128}$/.test(name.trim()) || !value.length || value.length > 16384) { invalid('请填写有效凭证名称和 1–16384 字符的明文'); return; }
    if (await save(name.trim(), value)) { setValue(''); setName(''); }
  }
  return <section className="space-y-3" aria-label="凭证管理"><h2 className="text-lg font-semibold">凭证引用</h2><p>明文永不回读；仅显示引用、指纹和元数据。使用 secret:// 引用，不要把明文放入配置内容。</p>
    <form aria-label="凭证写入" className="space-y-3" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <TextField value={name} isDisabled={pending} isRequired onChange={(v) => { edited(); setName(v); }}><Label>凭证名称</Label><Input maxLength={128} autoComplete="off" /></TextField>
      <TextField value={value} isDisabled={pending} isRequired isInvalid={Boolean(errors.value)} onChange={(v) => { edited(); setValue(v); }}><Label>凭证明文</Label><Input type="password" maxLength={16384} autoComplete="new-password" /><FieldError>{errors.value}</FieldError></TextField>
      <Button type="submit" isDisabled={disabled}>加密保存凭证</Button>
    </form>
    <Table><Table.ScrollContainer role="region" aria-label="凭证元数据表格"><Table.Content aria-label="凭证元数据" className="min-w-[44rem]"><Table.Header>
      <Table.Column isRowHeader>引用</Table.Column><Table.Column>指纹</Table.Column><Table.Column>更新时间</Table.Column><Table.Column>归档时间</Table.Column><Table.Column>操作</Table.Column>
    </Table.Header><Table.Body renderEmptyState={() => '暂无凭证'}>{secrets.map((secret) => <Table.Row key={secret.name} id={secret.name}>
      <Table.Cell><code className="block w-56 break-all">{secret.ref}</code></Table.Cell><Table.Cell>{secret.fingerprint}</Table.Cell><Table.Cell>{secret.updatedAt}</Table.Cell><Table.Cell>{secret.retiredAt ?? '有效'}</Table.Cell>
      <Table.Cell focusMode="child"><Button size="sm" variant="secondary" isDisabled={disabled} onPress={() => lifecycle(secret)}>{secret.retiredAt ? '恢复凭证' : '归档凭证'} {secret.name}</Button></Table.Cell>
    </Table.Row>)}</Table.Body></Table.Content></Table.ScrollContainer></Table>
  </section>;
}
