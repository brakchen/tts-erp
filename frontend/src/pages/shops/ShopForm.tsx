import { useState } from 'react';
import { Button, FieldError, Input, Label, TextField } from '@heroui/react';
import type { Shop } from './contracts';

export type ShopFormKind = 'register' | 'edit' | 'app';
export interface ShopDraft {
  shop_id: string; account_name: string; region: string; opened_date: string;
  service_id: string; app_key: string; app_secret: string;
}
export const fieldLabels: Record<keyof ShopDraft, string> = {
  shop_id: '店铺 ID', account_name: '店铺名称', region: '区域', opened_date: '开店日期',
  service_id: 'Service ID', app_key: 'App Key', app_secret: 'App Secret',
};

export default function ShopForm({ kind, shop, pending, blocked, errors, onSubmit, onCancel }: {
  kind: ShopFormKind; shop?: Shop; pending: boolean; blocked: boolean; errors: Record<string, string>;
  onSubmit: (draft: ShopDraft) => void; onCancel: () => void;
}) {
  const [draft, setDraft] = useState<ShopDraft>({ shop_id: shop?.shop_id ?? '', account_name: shop?.account_name ?? '',
    region: shop?.region ?? '', opened_date: shop?.opened_date ?? '', service_id: shop?.service_id ?? '', app_key: '', app_secret: '' });
  const fields: (keyof ShopDraft)[] = kind === 'app' ? ['service_id', 'app_key', 'app_secret']
    : kind === 'edit' ? ['account_name', 'region', 'opened_date', 'service_id']
      : ['shop_id', 'account_name', 'region', 'opened_date', 'service_id', 'app_key', 'app_secret'];
  return <form aria-label="店铺表单" className="space-y-3" onSubmit={(event) => { event.preventDefault(); if (!pending && !blocked) onSubmit(draft); }}>
    {kind === 'edit' && <p>留空表示保持原值，不能清空既有元信息。</p>}
    {kind !== 'edit' && <p>App 凭据按 Service ID 共享；修改会影响使用同一 Service ID 的店铺。App Key 与 App Secret 必须成对填写；不会回显已有密钥。</p>}
    {fields.map((key) => <TextField key={key} name={key} value={draft[key]} isDisabled={pending} isInvalid={Boolean(errors[key])}
      isRequired={(kind === 'register' && key === 'shop_id') || (kind === 'app' && ['service_id', 'app_key', 'app_secret'].includes(key))}
      onChange={(value) => setDraft((previous) => ({ ...previous, [key]: value }))}>
      <Label>{fieldLabels[key]}</Label>
      <Input type={key === 'app_secret' ? 'password' : key === 'opened_date' ? 'date' : 'text'}
        autoComplete={key === 'app_secret' ? 'new-password' : 'off'} inputMode={key === 'shop_id' ? 'numeric' : undefined}
        maxLength={key === 'app_secret' ? 512 : key === 'app_key' ? 128 : key === 'account_name' ? 200 : key === 'region' ? 16 : 64} />
      <FieldError>{errors[key]}</FieldError>
    </TextField>)}
    <div className="flex flex-wrap gap-3"><Button type="submit" isDisabled={pending || blocked}>保存店铺</Button><Button type="button" variant="secondary" isDisabled={pending} onPress={onCancel}>取消</Button></div>
  </form>;
}
