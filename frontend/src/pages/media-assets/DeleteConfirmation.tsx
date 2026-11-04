import { useRef, useState } from 'react';
import { AlertDialog, Button } from '@heroui/react';

interface DeleteConfirmationProps {
  label: string;
  name: string;
  description: string;
  isDisabled: boolean;
  onConfirm: () => Promise<void>;
}

export default function DeleteConfirmation({
  label, name, description, isDisabled, onConfirm,
}: DeleteConfirmationProps) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const submitting = useRef(false);

  async function confirm() {
    if (isDisabled || submitting.current) return;
    submitting.current = true;
    setPending(true);
    try {
      await onConfirm();
      setOpen(false);
    } finally {
      submitting.current = false;
      setPending(false);
    }
  }

  return (
    <AlertDialog isOpen={open} onOpenChange={(next) => {
      if (!submitting.current) setOpen(next);
    }}>
      <Button variant="danger" isDisabled={isDisabled}>{label}</Button>
      <AlertDialog.Backdrop isKeyboardDismissDisabled={pending}>
        <AlertDialog.Container>
          <AlertDialog.Dialog>
            <AlertDialog.Header>
              <AlertDialog.Heading>确认{label}</AlertDialog.Heading>
            </AlertDialog.Header>
            <AlertDialog.Body>
              <p className="break-all font-semibold">{name}</p>
              <p>{description}</p>
            </AlertDialog.Body>
            <AlertDialog.Footer>
              <Button variant="secondary" autoFocus isDisabled={pending} onPress={() => setOpen(false)}>取消</Button>
              <Button variant="danger" isDisabled={isDisabled} isPending={pending} onPress={() => void confirm()}>
                {pending ? '删除中…' : '确认删除'}
              </Button>
            </AlertDialog.Footer>
          </AlertDialog.Dialog>
        </AlertDialog.Container>
      </AlertDialog.Backdrop>
    </AlertDialog>
  );
}
