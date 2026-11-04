import { useRef, useState, type ReactNode } from 'react';
import { AlertDialog, Button } from '@heroui/react';
import { mediaError, useLeaveGuard } from './media-ui';

export default function DeleteConfirmation({ label, name, description, isDisabled, onConfirm, remove = false, confirmDisabled, onVisibilityChange }: {
  label: string;
  name: string;
  description: ReactNode;
  isDisabled?: boolean;
  onConfirm: () => Promise<void>;
  remove?: boolean;
  confirmDisabled?: boolean;
  onVisibilityChange?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const submitting = useRef(false);
  useLeaveGuard(pending);

  function toggle(next: boolean) {
    setOpen(next);
    onVisibilityChange?.(next);
  }

  async function confirm() {
    if (isDisabled || confirmDisabled || submitting.current) return;
    submitting.current = true;
    setPending(true);
    setError('');
    try {
      await onConfirm();
      toggle(false);
    } catch (failure) {
      setError(mediaError(failure, true));
    } finally {
      submitting.current = false;
      setPending(false);
    }
  }

  return <AlertDialog isOpen={open} onOpenChange={(next) => {
    if (submitting.current) return;
    setError('');
    toggle(next);
  }}>
    <Button variant={remove ? 'secondary' : 'danger'} isDisabled={isDisabled} aria-label={label}>
      {remove ? '从当前数字人移除' : label}
    </Button>
    <AlertDialog.Backdrop isDismissable={!pending} isKeyboardDismissDisabled={pending}>
      <AlertDialog.Container><AlertDialog.Dialog>
        <AlertDialog.Header><AlertDialog.Heading>{remove ? '从当前数字人移除资产？' : '确认' + label}</AlertDialog.Heading></AlertDialog.Header>
        <AlertDialog.Body>
          <p className="break-words font-semibold">{name}</p>
          {open && <div className="space-y-2">{description}</div>}
          {error && <p role="alert">{error}</p>}
        </AlertDialog.Body>
        <AlertDialog.Footer>
          <Button variant="secondary" autoFocus isDisabled={pending} onPress={() => toggle(false)}>取消</Button>
          <Button variant="danger" isDisabled={isDisabled || confirmDisabled} isPending={pending} onPress={() => void confirm()}>
            {pending ? '处理中…' : remove ? '确认移除' : '确认删除'}
          </Button>
        </AlertDialog.Footer>
      </AlertDialog.Dialog></AlertDialog.Container>
    </AlertDialog.Backdrop>
  </AlertDialog>;
}
