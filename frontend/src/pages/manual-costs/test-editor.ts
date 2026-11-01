import { fireEvent, screen, within } from '@testing-library/react';

/** Exercise the single row-editing entry point through the real modal. */
export function openRowEditor(row: HTMLElement) {
  fireEvent.click(within(row).getByRole('button', { name: '编辑详情' }));
  return within(screen.getByRole('dialog'));
}

export function closeRowEditor() {
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '取消并保留草稿' }));
}

export function editRowCost(row: HTMLElement, value: string) {
  const dialog = openRowEditor(row);
  fireEvent.change(dialog.getByRole('textbox', { name: '单位成本' }), { target: { value } });
  closeRowEditor();
}

export function readRowCost(row: HTMLElement) {
  const dialog = openRowEditor(row);
  const value = (dialog.getByRole('textbox', { name: '单位成本' }) as HTMLInputElement).value;
  closeRowEditor();
  return value;
}
