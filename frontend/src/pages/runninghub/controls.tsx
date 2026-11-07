import type { ReactNode } from 'react';
import { Checkbox, Input, Label, ListBox, Modal, Select, TextField } from '@heroui/react';

export function Choice({ label, value, options, onChange, disabled }: {
  label: string;
  value: string;
  options: { id: string; name: string }[];
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return <Select isDisabled={disabled} selectedKey={value || null} onSelectionChange={(key) => onChange(String(key ?? ''))}>
    <Label>{label}</Label>
    <Select.Trigger><Select.Value /><Select.Indicator /></Select.Trigger>
    <Select.Popover><ListBox>{options.map((option) => (
      <ListBox.Item key={option.id} id={option.id} textValue={option.name}>{option.name}</ListBox.Item>
    ))}</ListBox></Select.Popover>
  </Select>;
}

export function Field({ label, value, onChange, disabled }: {
  label: string; value: string; onChange: (value: string) => void; disabled?: boolean;
}) {
  return <TextField isDisabled={disabled} value={value} onChange={onChange}><Label>{label}</Label><Input /></TextField>;
}

type CheckProps = {
  label: string; selected: boolean; onChange: (checked: boolean) => void; disabled?: boolean;
};

export function Check({ label, selected, onChange, disabled }: CheckProps) {
  return <Checkbox isDisabled={disabled} isSelected={selected} onChange={onChange}>
    <Checkbox.Content><Checkbox.Control><Checkbox.Indicator /></Checkbox.Control><Label>{label}</Label></Checkbox.Content>
  </Checkbox>;
}

export function SelectTile({ label, selected, onChange, disabled, children }: CheckProps & { children: ReactNode }) {
  return <Checkbox aria-label={label} className="rh-tile" data-checked={selected ? '' : undefined}
    isDisabled={disabled} isSelected={selected} onChange={onChange}>
    <Checkbox.Content className="rh-tile-content">
      {children}
      <Checkbox.Control className="rh-tile-check"><Checkbox.Indicator /></Checkbox.Control>
    </Checkbox.Content>
  </Checkbox>;
}

export function WorkModal({ title, busy, close, children, footer }: {
  title: string; busy: boolean; close: () => void; children: ReactNode; footer?: ReactNode;
}) {
  return <Modal.Backdrop isOpen isDismissable={!busy} isKeyboardDismissDisabled={busy}
    onOpenChange={(open) => { if (!open && !busy) close(); }}>
    <Modal.Container size="lg" scroll="inside">
      <Modal.Dialog>
        {!busy && <Modal.CloseTrigger aria-label="关闭弹窗" />}
        <Modal.Header><Modal.Heading>{title}</Modal.Heading></Modal.Header>
        <Modal.Body className="space-y-3">{children}</Modal.Body>
        {footer && <Modal.Footer>{footer}</Modal.Footer>}
      </Modal.Dialog>
    </Modal.Container>
  </Modal.Backdrop>;
}
