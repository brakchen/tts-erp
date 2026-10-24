import { useId } from 'react';
import { Input } from '@heroui/react';
import type { Fields } from './contracts';

export default function CostFields({ fields, errors, disabled, onChange }: { fields: Fields; errors: Record<string, string>; disabled: boolean; onChange: (key: keyof Fields, value: string) => void }) {
  const id = useId();
  return <div className="space-y-3">{([
    ['unit_cost', '单位成本'], ['currency', '货币'], ['note', '备注'], ['valid_from', '生效时间'],
  ] as const).map(([key, label]) => <div key={key} className="space-y-1">
    <label htmlFor={`${id}-${key}`}>{label}</label>
    <Input id={`${id}-${key}`} value={fields[key]} disabled={disabled} aria-invalid={Boolean(errors[key])} aria-describedby={errors[key] ? `${id}-${key}-error` : undefined}
      onChange={(event) => onChange(key, event.target.value)} />
    {errors[key] && <p id={`${id}-${key}-error`}>{errors[key]}</p>}
  </div>)}<p>金额按输入字符串提交，由服务端校验；货币不换算。生效时间可留空使用服务端当前 UTC；填写时须为带 Z 或明确偏移的 ISO 时间，例如 2026-10-24T12:00:00+07:00。</p></div>;
}
