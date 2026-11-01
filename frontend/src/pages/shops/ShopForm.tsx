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

export default function ShopForm({ kind, shop, pending, blocked, errors, submitLabel = '保存', onSubmit, onCancel, onFieldEdit }: {
  kind: ShopFormKind; shop?: Shop; pending: boolean; blocked: boolean; errors: Record<string, string>; submitLabel?: string;
  onSubmit: (draft: ShopDraft) => void; onCancel: () => void; onFieldEdit: (field: keyof ShopDraft) => void;
}) {
  const [draft, setDraft] = useState<ShopDraft>({ shop_id: shop?.shop_id ?? '', account_name: shop?.account_name ?? '',
    region: shop?.region ?? '', opened_date: shop?.opened_date ?? '', service_id: shop?.service_id ?? '', app_key: '', app_secret: '' });
  const fields: (keyof ShopDraft)[] = kind === 'app' ? ['service_id', 'app_key', 'app_secret']
    : kind === 'edit' ? ['opened_date'] : ['shop_id', 'account_name', 'region', 'opened_date'];
  const needsPair = kind === 'app' && !shop?.app_credentials_configured;
  return <form aria-label="店铺表单" className="space-y-4" onSubmit={(event) => { event.preventDefault(); if (!pending && !blocked) onSubmit(draft); }}>
    {shop && <div className="rounded-xl border border-[var(--rule)] bg-[var(--paper-deep)] p-4">
      <p className="font-semibold">{shop.account_name ?? '未取得店铺名称'}</p>
      <p className="erp-table-meta break-all">{shop.shop_id} · {shop.region ?? '区域待同步'}</p>
    </div>}
    {kind === 'register' && <p className="erp-table-meta">插件会自动登记当前绑定店铺，也可以在这里手工补录。名称和区域保存后只读，App 可稍后配置。</p>}
    {kind === 'edit' && <p className="erp-table-meta">店铺名称和区域只读。开店日期留空并保存，可清除原日期。</p>}
    {kind === 'app' && <p className="erp-table-meta">配置仅用于当前店铺。{needsPair ? '首次配置需填写完整 App Key 和 App Secret。' : '密钥留空表示保留；更换时请成对填写。切换 Service ID 时需提供对应配置。'} App Secret 不会回显。</p>}
    {fields.map((key) => <TextField key={key} name={key} value={draft[key]} isDisabled={pending} isInvalid={Boolean(errors[key])}
      isRequired={(kind === 'register' && key === 'shop_id') || (kind === 'app' && (key === 'service_id' || (needsPair && (key === 'app_key' || key === 'app_secret'))))}
      onChange={(value) => { onFieldEdit(key); setDraft((previous) => ({ ...previous, [key]: value })); }}>
      <Label>{fieldLabels[key]}</Label>
      <Input type={key === 'app_secret' ? 'password' : key === 'opened_date' ? 'date' : 'text'}
        autoComplete={key === 'app_secret' ? 'new-password' : 'off'} inputMode={key === 'shop_id' ? 'numeric' : undefined}
        maxLength={key === 'app_secret' ? 512 : key === 'app_key' ? 128 : key === 'account_name' ? 200 : key === 'region' ? 16 : 64} />
      <FieldError>{errors[key]}</FieldError>
    </TextField>)}
    <div className="flex flex-wrap justify-end gap-3"><Button type="button" variant="secondary" isDisabled={pending} onPress={onCancel}>取消</Button><Button type="submit" isDisabled={pending || blocked}>{pending ? '保存中…' : submitLabel}</Button></div>
  </form>;
}