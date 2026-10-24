import { useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import JsonEditor from './JsonEditor';

const exact = '{"integer":900719925474099312345,"decimal":0.123456789012345678901,"zero":0,"nil":null}';
function Harness({ initial = exact, disabled = false }: { initial?: string; disabled?: boolean }) {
  const [value, setValue] = useState(initial);
  return <JsonEditor label="TEST 配置" value={value} onChange={setValue} disabled={disabled} />;
}
// jsdom has no layout engine; these geometry stubs do not prove native layout.
const rects = Object.getOwnPropertyDescriptor(Range.prototype, 'getClientRects');
const bounds = Object.getOwnPropertyDescriptor(Range.prototype, 'getBoundingClientRect');
beforeAll(() => {
  Object.defineProperty(Range.prototype, 'getClientRects', { configurable: true, value: () => [] });
  Object.defineProperty(Range.prototype, 'getBoundingClientRect', { configurable: true, value: () => new DOMRect() });
});
afterAll(() => {
  if (rects) Object.defineProperty(Range.prototype, 'getClientRects', rects); else Reflect.deleteProperty(Range.prototype, 'getClientRects');
  if (bounds) Object.defineProperty(Range.prototype, 'getBoundingClientRect', bounds); else Reflect.deleteProperty(Range.prototype, 'getBoundingClientRect');
});
describe('runtime-configs public editor adapter (real SDK, jsdom geometry)', () => {
  it('shows exact integer and decimal tokens in code, tree and readOnly view and preserves them through formatting', async () => {
    render(<Harness />);
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'TEST 配置 编辑器' })).toHaveTextContent('0.123456789012345678901'));
    fireEvent.click(screen.getByRole('button', { name: 'TEST 配置 tree' }));
    await waitFor(() => expect(screen.getByRole('tree')).toHaveTextContent('900719925474099312345'));
    expect(screen.getByRole('tree')).toHaveTextContent('0.123456789012345678901');
    fireEvent.click(screen.getByRole('button', { name: 'TEST 配置 view' }));
    expect(await screen.findByRole('tree')).toHaveTextContent('0.123456789012345678901');
    fireEvent.click(screen.getByRole('button', { name: 'TEST 配置 code' }));
    fireEvent.click(screen.getByRole('button', { name: 'TEST 配置 格式化' }));
    fireEvent.click(screen.getByRole('button', { name: 'TEST 配置 压缩' }));
    expect(screen.getByLabelText('TEST 配置 JSON 文本')).toHaveValue(exact);
  });
  it('retains invalid canonical text across rejected tree conversion and disabled/enabled transitions', async () => {
    const view = render(<Harness initial="{TEST invalid" />);
    fireEvent.click(screen.getByRole('button', { name: 'TEST 配置 tree' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('无效文本已保留');
    expect(screen.getByLabelText('TEST 配置 JSON 文本')).toHaveValue('{TEST invalid');
    view.rerender(<Harness initial="{TEST invalid" disabled />);
    expect(screen.getByLabelText('TEST 配置 JSON 文本')).toHaveAttribute('readonly');
    view.rerender(<Harness initial="{TEST invalid" />);
    expect(screen.getByLabelText('TEST 配置 JSON 文本')).toHaveValue('{TEST invalid');
    fireEvent.change(screen.getByLabelText('TEST 配置 JSON 文本'), { target: { value: exact } });
    fireEvent.click(screen.getByRole('button', { name: 'TEST 配置 tree' }));
    await waitFor(() => expect(screen.getByRole('tree')).toHaveTextContent('0.123456789012345678901'));
  });
  it('locks view mode edits in both the SDK and the accessible text path', async () => {
    render(<Harness />); fireEvent.click(screen.getByRole('button', { name: 'TEST 配置 view' }));
    const editor = screen.getByRole('region', { name: 'TEST 配置' });
    expect(within(editor).getByLabelText('TEST 配置 JSON 文本')).toHaveAttribute('readonly');
  });
});
