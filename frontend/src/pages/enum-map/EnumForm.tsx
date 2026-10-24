import { useState } from 'react';
import { Button, FieldError, Input, Label, TextField } from '@heroui/react';
import type { EnumRow } from './contracts';

export interface EnumDraft { enum_value: string; label_zh: string; sort_order: string }
export const fieldLabels: Record<string, string> = { enum_type: '分类', enum_value: '英文值', label_zh: '中文标签', sort_order: '排序' };
export default function EnumForm({ row, pending, blocked, errors, submitLabel, onSubmit, onCancel, onFieldEdit }: {
  row?: EnumRow; pending: boolean; blocked: boolean; errors: Record<string, string>; submitLabel: string;
  onSubmit: (draft: EnumDraft) => void; onCancel: () => void; onFieldEdit: (field: keyof EnumDraft) => void;
}) {
  const [draft, setDraft] = useState<EnumDraft>({ enum_value: row?.enum_value ?? '', label_zh: row?.label_zh ?? '', sort_order: String(row?.sort_order ?? 0) });
  return <form aria-label="枚举表单" className="space-y-3" onSubmit={(event) => { event.preventDefault(); if (!pending && !blocked) onSubmit(draft); }}>
    {(['enum_value', 'label_zh', 'sort_order'] as const).map((field) => <TextField key={field} name={field} value={draft[field]} isRequired
      isReadOnly={field === 'enum_value' && row !== undefined} isDisabled={pending} isInvalid={Boolean(errors[field])}
      onChange={(value) => { onFieldEdit(field); setDraft((previous) => ({ ...previous, [field]: value })); }}>
      <Label>{fieldLabels[field]}</Label><Input inputMode={field === 'sort_order' ? 'numeric' : undefined} autoComplete="off" />
      <FieldError>{errors[field]}</FieldError>
    </TextField>)}
    {row === undefined && <p>相同分类和英文值已存在时，此操作会更新其中文标签与排序。</p>}
    <p>英文值创建后只读；排序填写非负整数，显示顺序以服务端返回为准。</p>
    <div className="flex flex-wrap gap-3"><Button type="submit" isDisabled={pending || blocked}>{submitLabel}</Button><Button type="button" variant="secondary" isDisabled={pending} onPress={onCancel}>取消</Button></div>
  </form>;
}
