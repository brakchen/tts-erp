import { Description, FieldError, Input, Label, TextArea, TextField } from '@heroui/react';
import type { Fields } from './contracts';

export default function CostFields({ fields, errors, disabled, onChange }: { fields: Fields; errors: Record<string, string>; disabled: boolean; onChange: (key: keyof Fields, value: string) => void }) {
  return <div className="grid min-w-0 gap-5 sm:grid-cols-2">
    <TextField className="min-w-0" name="unit_cost" value={fields.unit_cost} onChange={(value) => onChange('unit_cost', value)} isDisabled={disabled} isInvalid={Boolean(errors.unit_cost)} validationBehavior="aria">
      <Label>单位成本</Label>
      <Input fullWidth inputMode="decimal" placeholder="例如 25.0000" className="min-h-11" />
      <Description>按输入金额保存，保留原始精度。</Description><FieldError>{errors.unit_cost}</FieldError>
    </TextField>
    <TextField className="min-w-0" name="currency" value={fields.currency} onChange={(value) => onChange('currency', value)} isDisabled={disabled} isInvalid={Boolean(errors.currency)} validationBehavior="aria">
      <Label>货币</Label>
      <Input fullWidth placeholder="例如 CNY" className="min-h-11" />
      <Description>与成本金额对应，不进行货币换算。</Description><FieldError>{errors.currency}</FieldError>
    </TextField>
    <TextField className="min-w-0 sm:col-span-2" name="note" value={fields.note} onChange={(value) => onChange('note', value)} isDisabled={disabled} isInvalid={Boolean(errors.note)} validationBehavior="aria">
      <Label>备注</Label><TextArea fullWidth rows={3} placeholder="选填，记录成本调整的原因" /><FieldError>{errors.note}</FieldError>
    </TextField>
    <TextField className="min-w-0 sm:col-span-2" name="valid_from" value={fields.valid_from} onChange={(value) => onChange('valid_from', value)} isDisabled={disabled} isInvalid={Boolean(errors.valid_from)} validationBehavior="aria">
      <Label>生效时间</Label><Input fullWidth placeholder="留空即使用保存时的服务端时间" className="min-h-11" />
      <Description>选填，留空使用服务端当前 UTC。指定时间需带时区，例如 2026-10-24T12:00:00+07:00。</Description><FieldError>{errors.valid_from}</FieldError>
    </TextField>
  </div>;
}
