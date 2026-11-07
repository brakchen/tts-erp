import type { ReactNode } from 'react';
import { AlertDialog, Button } from '@heroui/react';
import { ActionError } from './media-ui';

/** The caller owns the write lock and result. */
export default function OperationConfirmation({ title, description, children, pending = false, error = '',
  confirmLabel = '确认删除', danger = true, confirmDisabled = false, onConfirm, onCancel,
}: {
  title: string; description: ReactNode; children?: ReactNode; pending?: boolean; error?: string;
  confirmLabel?: string; danger?: boolean; confirmDisabled?: boolean;
  onConfirm: () => void | Promise<void>; onCancel: () => void;
}) {
  return <AlertDialog.Backdrop isOpen isDismissable={false} isKeyboardDismissDisabled={pending}
    onOpenChange={(open) => { if (!open && !pending) onCancel(); }}>
    <AlertDialog.Container><AlertDialog.Dialog>
      <AlertDialog.Header><AlertDialog.Heading>{title}</AlertDialog.Heading></AlertDialog.Header>
      <AlertDialog.Body className="media-stack">
        <p>{description}</p>
        {children}
        <ActionError message={error} />
      </AlertDialog.Body>
      <AlertDialog.Footer>
        <Button variant="secondary" autoFocus isDisabled={pending} onPress={onCancel}>取消</Button>
        <Button variant={danger ? 'danger' : 'primary'} isDisabled={confirmDisabled} isPending={pending}
          onPress={() => void onConfirm()}>{confirmLabel}</Button>
      </AlertDialog.Footer>
    </AlertDialog.Dialog></AlertDialog.Container>
  </AlertDialog.Backdrop>;
}
