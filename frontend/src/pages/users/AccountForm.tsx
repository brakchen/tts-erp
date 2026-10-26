import { Button, Checkbox, FieldError, Input, Label, ListBox, Select, TextField } from '@heroui/react';
import type { Catalog, CommandKind, Role, User } from './contracts';
import { labels } from './contracts';

export interface Draft {
  username: string; displayName: string; password: string; newPassword: string;
  roles: string[]; code: string; name: string; apiTier: string; permissions: string[];
}
export const initialDraft = (user?: User, role?: Role): Draft => ({
  username: '', displayName: user?.displayName ?? '', password: '', newPassword: '',
  roles: user?.roles ?? [], code: role?.code ?? '', name: role?.name ?? '',
  apiTier: role?.apiTier ?? 'readwrite', permissions: role?.permissions ?? [],
});

export function AccountForm({ kind, draft, role, catalog, pending, blocked, errors, retry, onEdit, onSubmit, onCancel }: {
  kind: CommandKind; draft: Draft; role?: Role; catalog: Catalog; pending: boolean;
  blocked: boolean; errors: Record<string, string>; retry: boolean;
  onEdit: (draft: Draft) => void; onSubmit: () => void; onCancel: () => void;
}) {
  const fields: (keyof Pick<Draft, 'username' | 'displayName' | 'password' | 'newPassword' | 'code' | 'name'>)[] =
    kind === 'createUser' ? ['username', 'displayName', 'password'] : kind === 'displayName' ? ['displayName'] :
      kind === 'password' ? ['newPassword'] : kind === 'createRole' ? ['code', 'name'] : kind === 'editRole' ? ['name'] : [];
  function toggle(key: 'roles' | 'permissions', code: string, checked: boolean) {
    onEdit({ ...draft, [key]: checked ? [...draft[key], code] : draft[key].filter((item) => item !== code) });
  }
  return <form aria-label="账号维护表单" className="space-y-4"
    onSubmit={(event) => { event.preventDefault(); if (!blocked) onSubmit(); }}>
    {fields.map((key) => <TextField key={key} name={key} value={draft[key]} isRequired
      isDisabled={pending} isInvalid={Boolean(errors[key])} onChange={(value) => onEdit({ ...draft, [key]: value })}>
      <Label>{labels[key]}</Label>
      <Input type={key === 'password' || key === 'newPassword' ? 'password' : 'text'}
        autoComplete={key === 'password' || key === 'newPassword' ? 'new-password' : 'off'}
        maxLength={key === 'password' || key === 'newPassword' ? 128 : key === 'username' || key === 'code' ? 32 : 64} />
      <FieldError>{errors[key]}</FieldError>
    </TextField>)}
    {kind === 'createUser' && <p>用户名创建后不可修改。密码至少 6 位，需包含大小写字母和数字，最终由服务端校验。</p>}
    {kind === 'password' && <p>新密码不会回显或保存到浏览器存储。重置将吊销该用户的全部会话，请线下安全告知用户。</p>}
    {(kind === 'createUser' || kind === 'roles') && <fieldset className="space-y-2">
      <legend>角色（可多选）</legend>
      {catalog.roles.length === 0 && <p>暂无角色</p>}
      {catalog.roles.map((item) => <Checkbox key={item.code} isSelected={draft.roles.includes(item.code)}
        isDisabled={pending} onChange={(checked) => toggle('roles', item.code, checked)}>
        <Checkbox.Control><Checkbox.Indicator /></Checkbox.Control>
        <Checkbox.Content>{item.name}（{item.code}）</Checkbox.Content>
      </Checkbox>)}
      {errors.roles && <p>{errors.roles}</p>}
    </fieldset>}
    {(kind === 'createRole' || kind === 'editRole') && <>
      {role && <p>角色代码：{role.code}（创建后不可修改）</p>}
      <Select value={draft.apiTier} isDisabled={pending || role?.isBuiltin}
        onChange={(key) => { if (typeof key === 'string') onEdit({ ...draft, apiTier: key }); }}>
        <Label>API 档位</Label>
        <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
        <Select.Popover><ListBox>
          {['readonly', 'readwrite', 'admin'].map((tier) => <ListBox.Item key={tier} id={tier} textValue={tier}>{tier}</ListBox.Item>)}
        </ListBox></Select.Popover>
      </Select>
      {role?.isBuiltin && <p>内置角色不可更改 API 档位。</p>}
      <div className="max-h-72 overflow-auto space-y-3">
        {[...new Set(catalog.allPermissions.map((p) => p.group))].map((group) => <fieldset key={group} className="space-y-2">
          <legend>{group}</legend>
          {catalog.allPermissions.filter((p) => p.group === group).map((p) => <Checkbox key={p.code}
            isSelected={draft.permissions.includes(p.code)} isDisabled={pending}
            onChange={(checked) => toggle('permissions', p.code, checked)}>
            <Checkbox.Control><Checkbox.Indicator /></Checkbox.Control>
            <Checkbox.Content>{p.label}（{p.code}）</Checkbox.Content>
          </Checkbox>)}
        </fieldset>)}
      </div>
      {catalog.allPermissions.length === 0 && <p>权限点目录为空</p>}
      {errors.permissions && <p>{errors.permissions}</p>}
    </>}
    <div className="flex flex-wrap gap-3">
      <Button type="submit" isDisabled={blocked}>{retry ? '明确再次提交' : kind === 'password' ? '重置密码并吊销会话' : kind === 'createUser' ? '创建用户' : '保存'}</Button>
      <Button variant="secondary" type="button" isDisabled={pending} onPress={onCancel}>取消</Button>
    </div>
  </form>;
}
