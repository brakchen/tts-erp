import { useEffect, useRef, useState } from 'react';
import { Button } from '@heroui/react';
import { createJSONEditor, Mode, type Content, type JSONParser } from 'vanilla-jsoneditor';
import { stringify } from 'lossless-json';
import { parseRuntime, stringifyRuntime } from './json';

const parser: JSONParser = { parse: parseRuntime, stringify };
type View = 'code' | 'tree' | 'view';

/** vanilla-jsoneditor 3.13.0 (ISC), https://github.com/josdejong/svelte-jsoneditor.
 * Public factory, controlled content, parser and readOnly APIs only. Canonical
 * text stays React-owned, including invalid input; no automatic JSON repair.
 */
export default function JsonEditor({ label, value, onChange, readOnly = false, disabled = false }: {
  label: string; value: string; onChange?: (value: string) => void; readOnly?: boolean; disabled?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const editor = useRef<ReturnType<typeof createJSONEditor> | undefined>(undefined);
  const changed = useRef(onChange); changed.current = onChange;
  const locked = useRef(readOnly || disabled);
  const canonical = useRef(value); canonical.current = value;
  const lastText = useRef(value);
  const [error, setError] = useState('');
  const [mode, setMode] = useState<View>(readOnly ? 'view' : 'code');
  locked.current = readOnly || disabled || mode === 'view';
  useEffect(() => {
    if (!host.current) return;
    const instance = createJSONEditor({ target: host.current, props: {
      content: { text: canonical.current }, mode: readOnly ? Mode.tree : Mode.text,
      readOnly: locked.current, ariaLabel: `${label} 编辑器`,
      parser, validationParser: parser, mainMenuBar: false, navigationBar: true, statusBar: true, askToFormat: false,
      onChange: (content: Content) => {
        if (locked.current) return;
        const text = 'text' in content ? content.text : stringifyRuntime(content.json, 2);
        lastText.current = text; changed.current?.(text); setError('');
      },
      onError: () => setError('JSON 转换失败；无效文本已保留，请先修正。'),
    } });
    editor.current = instance;
    return () => { editor.current = undefined; void instance.destroy(); };
  }, []);
  useEffect(() => {
    const instance = editor.current; if (!instance) return;
    instance.updateProps({ readOnly: readOnly || disabled || mode === 'view' });
    if (lastText.current !== value) {
      // A text edit may be invalid while the tree is visible. Preserve it and
      // return to code instead of accepting the editor's optional repair UI.
      try { parseRuntime(value); } catch {
        instance.updateProps({ mode: Mode.text }); setMode('code');
      }
      instance.updateProps({ content: { text: value } }); lastText.current = value;
    }
  }, [value, readOnly, disabled, mode]);
  function switchMode(next: View) {
    if ((readOnly || disabled) && next !== 'view') return;
    try {
      if (next !== 'code') parseRuntime(value);
      editor.current?.updateProps({ content: { text: value }, mode: next === 'code' ? Mode.text : Mode.tree, readOnly: readOnly || disabled || next === 'view' });
      lastText.current = value; setMode(next); setError('');
    } catch { setError('JSON 转换失败；无效文本已保留，请先修正。'); }
  }
  function format(space?: number) {
    if (locked.current) return;
    try { changed.current?.(stringifyRuntime(parseRuntime(value), space)); setError(''); }
    catch { setError('JSON 转换失败；无效文本已保留，请先修正。'); }
  }
  return <section aria-label={label} className="min-w-0 space-y-2">
    <h3 className="font-semibold">{label}</h3>
    <div className="flex flex-wrap gap-2" aria-label={`${label}视图切换`}>
      {(['code', 'tree', 'view'] as const).map((next) => <Button key={next} size="sm" variant="secondary" aria-pressed={mode === next} isDisabled={(readOnly || disabled) && next !== 'view'} onPress={() => switchMode(next)}>{label} {next}</Button>)}
      <Button size="sm" variant="secondary" isDisabled={locked.current} onPress={() => format(2)}>{label} 格式化</Button>
      <Button size="sm" variant="secondary" isDisabled={locked.current} onPress={() => format()}>{label} 压缩</Button>
    </div>
    <div ref={host} className="h-80 min-w-0 overflow-auto" />
    <details><summary>无障碍 JSON 文本编辑</summary><label className="block"><span>{label} JSON 文本</span><textarea aria-label={`${label} JSON 文本`} className="h-48 w-full font-mono" value={value} readOnly={locked.current} onChange={(event) => { if (!locked.current) onChange?.(event.target.value); }} spellCheck={false} /></label></details>
    {error && <p role="alert">{error}</p>}
  </section>;
}
