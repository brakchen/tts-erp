import type { ReactNode } from 'react';
import { Button } from '@heroui/react';
import { ActionError, RecordPage } from './media-ui';

/** The object page owns the write lock and result; this is its confirmation view. */
export default function OperationConfirmation({ title, description, children, pending = false, error = '',
  confirmLabel = '确认删除', danger = true, confirmDisabled = false, onConfirm, onCancel,
}: {
  title: string; description: ReactNode; children?: ReactNode; pending?: boolean; error?: string;
  confirmLabel?: string; danger?: boolean; confirmDisabled?: boolean;
  onConfirm: () => void | Promise<void>; onCancel: () => void;
}) {
  return <section aria-label="操作确认">
    <RecordPage eyebrow="操作确认" title={title} description={description}>
      <div className="media-box media-stack">
        {children}
        <ActionError message={error} />
        <div className="media-actions">
          <Button variant="secondary" isDisabled={pending} onPress={onCancel}>取消</Button>
          <Button variant={danger ? 'danger' : 'primary'} className={danger ? 'media-danger' : undefined}
            isDisabled={confirmDisabled} isPending={pending} onPress={() => void onConfirm()}>{confirmLabel}</Button>
        </div>
      </div>
    </RecordPage>
  </section>;
}
