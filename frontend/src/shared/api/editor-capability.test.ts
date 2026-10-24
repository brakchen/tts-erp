import { expect, it } from 'vitest';
import { waitFor, within } from '@testing-library/dom';
import { createJSONEditor, Mode, type Content, type JSONParser } from 'vanilla-jsoneditor';
import { parse, stringify } from 'lossless-json';

const parser: JSONParser = { parse, stringify };
const text = '{"id":900719925474099312345,"ratio":0.123456789012345678901,"zero":0}';
function encoded(content: Content): string {
  return 'text' in content ? content.text : stringify(content.json) ?? '';
}
it('keeps exact numeric values through public document updates, text and readonly view', async () => {
  const target = document.createElement('div'); document.body.append(target);
  let editor: ReturnType<typeof createJSONEditor> | undefined;
  editor = createJSONEditor({ target, props: { content: { text }, mode: Mode.tree,
    parser, validationParser: parser, mainMenuBar: false, statusBar: false, navigationBar: false,
    onChange: (content: Content) => editor?.updateProps({ content }) } });
  try {
    await Promise.resolve();
    // The public programmatic patch returns JSON; unlike an interactive tree
    // edit it does not emit onChange. Commit that result through public update.
    const patched = editor.patch([{ op: 'replace', path: '/zero', value: 1 }]);
    editor.update({ json: patched.json });
    const expected = text.replace('"zero":0', '"zero":1');
    await waitFor(() => expect(stringify(parse(encoded(editor!.get())))).toBe(expected));
    editor.updateProps({ mode: Mode.text });
    await waitFor(() => expect(within(target).getByRole('textbox')).toHaveTextContent('900719925474099312345'));
    expect(within(target).getByRole('textbox')).toHaveTextContent('0.123456789012345678901');
    expect(stringify(parse(encoded(editor.get())))).toBe(expected);
    editor.updateProps({ mode: Mode.tree, readOnly: true });
    await waitFor(() => expect(within(target).getByRole('tree')).toHaveTextContent('900719925474099312345'));
    expect(stringify(parse(encoded(editor.get())))).toBe(expected);
  } finally { await editor.destroy(); target.remove(); }
});
