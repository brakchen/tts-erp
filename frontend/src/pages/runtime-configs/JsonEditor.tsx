import { useEffect, useRef, useState } from 'react';
import { Button } from '@heroui/react';
import JSONEditor from 'jsoneditor';
import 'jsoneditor/dist/jsoneditor.css';
import { parseRuntime, stringifyRuntime } from './json';

/** JSONEditor 10.4.3, Apache-2.0, https://github.com/josdejong/jsoneditor.
 * Public mode/text APIs only. An equivalent labelled textarea keeps invalid text
 * and keyboard/screen-reader access without depending on Ace's internal DOM.
 */
export default function JsonEditor({ label, value, onChange, readOnly = false, disabled = false }: {
  label: string; value: string; onChange?: (value: string) => void; readOnly?: boolean; disabled?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const editor = useRef<JSONEditor | undefined>(undefined);
  const changed = useRef(onChange); changed.current = onChange;
  const locked = useRef(readOnly || disabled); locked.current = readOnly || disabled;
  const [error, setError] = useState('');
  const [mode, setMode] = useState<'code' | 'tree' | 'view'>(readOnly ? 'view' : 'code');
  useEffect(() => {
    if (!host.current) return;
    const instance = new JSONEditor(host.current, {
      mode: readOnly || disabled ? 'view' : 'code', modes: readOnly || disabled ? ['view'] : ['code', 'tree', 'view'],
      navigationBar: false, statusBar: true,
      JSON: { parse: parseRuntime, stringify: stringifyRuntime },
      onChangeText: (text: string) => { if (!locked.current) changed.current?.(text); },
      onError: () => setError('JSON 转换失败；无效文本已保留，请先修正。'),
    });
    editor.current = instance;
    return () => { editor.current = undefined; instance.destroy(); };
  }, [readOnly, disabled]);
  useEffect(() => {
    const instance = editor.current;
    if (instance && instance.getText() !== value) instance.setText(value);
  }, [value, readOnly, disabled]);
  function switchMode(next: 'code' | 'tree' | 'view') {
    try { editor.current?.setMode(next); setMode(next); setError(''); }
    catch { setError('JSON 转换失败；无效文本已保留，请先修正。'); }
  }
  return <section aria-label={label} className="min-w-0 space-y-2">
    <h3 className="font-semibold">{label}</h3>
    <div className="flex flex-wrap gap-2" aria-label={`${label}视图切换`}>
      {(['code', 'tree', 'view'] as const).map((next) => <Button key={next} size="sm" variant="secondary" aria-pressed={mode === next} isDisabled={(readOnly || disabled) && next !== 'view'} onPress={() => switchMode(next)}>{label} {next}</Button>)}
    </div>
    <div ref={host} className="h-80 min-w-0 overflow-auto" />
    <details><summary>无障碍 JSON 文本编辑</summary><label className="block"><span>{label} JSON 文本</span><textarea aria-label={`${label} JSON 文本`} className="h-48 w-full font-mono" value={value} readOnly={readOnly || disabled} onChange={(event) => onChange?.(event.target.value)} spellCheck={false} /></label></details>
    {error && <p role="alert">{error}</p>}
  </section>;
}
