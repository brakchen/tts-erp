import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Disclosure, Input, Label, Tag, TagGroup, TextField, buttonVariants } from '@heroui/react';
import { decodeTags } from './contracts';
import { ReadStatus, useMediaRead } from './media-ui';

/** Each input is one tag, including punctuation. Candidates stay server-owned. */
export function withPendingTag(selected: readonly string[], input: string, catalog: readonly string[] = []): string[] {
  const tags = new Map<string, string>();
  const pending = input.trim().replace(/\s+/gu, ' ');
  const saved = [...selected, ...catalog].find((tag) => tag.toLowerCase() === pending.toLowerCase());
  for (const value of [...selected, saved ?? pending]) {
    const tag = value.trim().replace(/\s+/gu, ' ');
    if (tag && !tags.has(tag.toLowerCase())) tags.set(tag.toLowerCase(), tag);
  }
  return Array.from(tags.values());
}

export default function TagEditor({ root, selected, input, onChange, onInput, onCatalog, disabled = false }: {
  root: string; selected: string[]; input: string; onChange: (tags: string[]) => void;
  onInput: (value: string) => void; onCatalog: (tags: string[]) => void; disabled?: boolean;
}) {
  const read = useMediaRead(root, '/assets/tags', decodeTags);
  const choices = withPendingTag([...(read.data ?? []), ...selected], '');
  const selection = withPendingTag(selected, '');
  const selectedKeys = new Set(selection.map((tag) => tag.toLowerCase()));
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const normalized = input.trim().replace(/\s+/gu, ' ');
  const existing = [...selection, ...choices].find((tag) => tag.toLowerCase() === normalized.toLowerCase());
  // Share the server catalog with the submit owner; preserve the raw draft
  // while typing, including spaces after a prefix that is an existing tag.
  useEffect(() => {
    if (read.data) onCatalog(read.data);
  }, [read.data, onCatalog]);
  function add() {
    if (disabled || read.loading || !read.data) return;
    setNotice('');
    if (!normalized) { setError('请输入标签名称。'); return; }
    const tag = existing ?? normalized;
    setError('');
    onChange(withPendingTag(selected, tag));
    onInput('');
    setNotice(existing ? '已选中现有标签「' + tag + '」。' : '已添加标签「' + tag + '」。');
  }
  return <div className="media-tag-editor">
    <TagGroup aria-label="可选素材标签" selectionMode="multiple" selectedKeys={selectedKeys}
      disabledKeys={disabled ? choices.map((tag) => tag.toLowerCase()) : []}
      onSelectionChange={(keys) => {
        const nextKeys = keys === 'all' ? choices.map((tag) => tag.toLowerCase()) : Array.from(keys, String);
        onChange(nextKeys.flatMap((key) => {
          const tag = [...selection, ...choices].find((value) => value.toLowerCase() === key);
          return tag ? [tag] : [];
        }));
      }}>
      <Label>素材标签</Label>
      <TagGroup.List renderEmptyState={() => <p className="text-sm text-muted">暂无标签，可在下方新增。</p>}>
        {choices.map((tag) => <Tag key={tag.toLowerCase()} id={tag.toLowerCase()} textValue={tag}
          className={'media-tag-option ' + (selectedKeys.has(tag.toLowerCase()) ? 'media-tag-selected' : '')}>
          <span className="media-tag-tick" aria-hidden="true">{selectedKeys.has(tag.toLowerCase()) ? '✓' : '＋'}</span>{tag}
        </Tag>)}
      </TagGroup.List>
    </TagGroup>
    <p className="media-tag-count" aria-live="polite">已选 {selection.length} 个 · 可多选，再次点击取消</p>
    <ReadStatus title="已有标签" loading={read.loading} error={read.error} onRetry={read.retry} />
    <div className="media-tag-add">
      <TextField value={input} onChange={(value) => { setError(''); setNotice(''); onInput(value); }}
        isDisabled={disabled} isInvalid={Boolean(error)} className="min-w-0 flex-1">
        <Label>新增标签</Label><Input placeholder="输入新标签名称，按回车添加" onKeyDown={(event) => {
          if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); if (!event.nativeEvent.isComposing) add(); }
        }} />
      </TextField>
      <Button variant="secondary" isDisabled={disabled || read.loading || !read.data} onPress={add}>添加标签</Button>
    </div>
    {error && <p className="media-error" role="alert">{error}</p>}
    {notice && <p className="media-muted" role="status">{notice}</p>}
  </div>;
}

export function UploadTags({ root, selected, onChange, disabled }: {
  root: string; selected: string[]; onChange: (tags: string[]) => void; disabled: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState('');
  const searchInput = useRef<HTMLInputElement>(null);
  const open = expanded && !disabled;
  const read = useMediaRead(root, open ? '/assets/tags' : null, decodeTags);
  const choices = withPendingTag([...(read.data ?? []), ...selected], '');
  const normalized = query.trim().replace(/\s+/gu, ' ');
  const key = normalized.toLowerCase();
  const existing = choices.find((tag) => tag.toLowerCase() === key);
  const matches = choices.filter((tag) => tag.toLowerCase().includes(key));
  const selectedKeys = new Set(selected.map((tag) => tag.toLowerCase()));

  function add() {
    if (!normalized || !read.data || read.loading || disabled) return;
    onChange(withPendingTag(selected, existing ?? normalized));
    setQuery('');
    searchInput.current?.focus();
  }

  return <fieldset className="grid min-w-0 gap-2" disabled={disabled}>
    <legend className="pb-2">素材标签 <span className="text-muted">· 可选</span></legend>
    {selected.length > 0 && <TagGroup aria-label="已选标签" disabledKeys={disabled ? selectedKeys : []}
      onRemove={disabled ? undefined : (keys) => onChange(selected.filter((tag) => !keys.has(tag.toLowerCase())))}>
      <TagGroup.List className="flex-wrap">
        {selected.map((tag) => <Tag key={tag.toLowerCase()} id={tag.toLowerCase()} textValue={tag} className="max-w-full [overflow-wrap:anywhere]">
          {tag}<Tag.RemoveButton aria-label="移除标签" />
        </Tag>)}
      </TagGroup.List>
    </TagGroup>}
    <Disclosure isExpanded={open} onExpandedChange={setExpanded} isDisabled={disabled}>
      <Disclosure.Heading>
        <Disclosure.Trigger className={buttonVariants({ variant: 'secondary', size: 'sm' })}>
          {open ? '收起标签' : '选择标签'}<Disclosure.Indicator />
        </Disclosure.Trigger>
      </Disclosure.Heading>
      <Disclosure.Content>
        {open && <div className="media-upload-tag-panel">
          <TextField aria-label="搜索标签" type="search" value={query} onChange={setQuery}>
            <Input ref={searchInput} autoFocus placeholder="搜索或新建标签" onKeyDown={(event) => {
              if (event.key !== 'Enter' || event.nativeEvent.isComposing) return;
              event.preventDefault();
              add();
            }} />
          </TextField>
          <ReadStatus title="已有标签" loading={read.loading} error={read.error} onRetry={read.retry} />
          {!key && read.data && !choices.length && <p className="text-muted">暂无已有标签。</p>}
          {matches.length > 0 && <div className="media-upload-tag-options" role="group" aria-label="可选标签">
            {matches.map((tag) => <Checkbox key={tag.toLowerCase()} isSelected={selectedKeys.has(tag.toLowerCase())}
              onChange={(checked) => onChange(checked ? withPendingTag(selected, tag) : selected.filter((value) => value !== tag))}>
              <Checkbox.Content><Checkbox.Control><Checkbox.Indicator /></Checkbox.Control>
                <Label className="min-w-0 [overflow-wrap:anywhere]">{tag}</Label>
              </Checkbox.Content>
            </Checkbox>)}
          </div>}
          {key && !existing && read.data && !read.loading && <Button type="button" variant="secondary" className="justify-self-start whitespace-normal [overflow-wrap:anywhere]"
            onPress={add}>新建「{normalized}」</Button>}
        </div>}
      </Disclosure.Content>
    </Disclosure>
  </fieldset>;
}
