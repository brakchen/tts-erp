import { useEffect, useState } from 'react';
import { Button, Input, Label, Tag, TagGroup, TextField } from '@heroui/react';
import { decodeTags } from './contracts';
import { ReadStatus, useMediaRead } from './media-ui';

/** Each input is one tag, including punctuation. Candidates stay server-owned. */
export function withPendingTag(selected: readonly string[], input: string): string[] {
  const tags = new Map<string, string>();
  for (const value of [...selected, input]) {
    const tag = value.trim().replace(/\s+/gu, ' ');
    if (tag && !tags.has(tag.toLowerCase())) tags.set(tag.toLowerCase(), tag);
  }
  return Array.from(tags.values());
}

export default function TagEditor({ root, selected, input, onChange, onInput, disabled = false }: {
  root: string; selected: string[]; input: string; onChange: (tags: string[]) => void;
  onInput: (value: string) => void; disabled?: boolean;
}) {
  const read = useMediaRead(root, '/assets/tags', decodeTags);
  const choices = withPendingTag([...(read.data ?? []), ...selected], '');
  const selection = withPendingTag(selected, '');
  const selectedKeys = new Set(selection.map((tag) => tag.toLowerCase()));
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const normalized = input.trim().replace(/\s+/gu, ' ');
  const existing = [...selection, ...choices].find((tag) => tag.toLowerCase() === normalized.toLowerCase());
  // Canonical saved spelling also reaches the parent's pending-input submit
  // path, including when candidates finish loading after the user has typed.
  useEffect(() => {
    if (existing && input !== existing) onInput(existing);
  }, [existing, input, onInput]);
  function add() {
    if (disabled) return;
    setNotice('');
    if (!normalized) { setError('请输入标签名称。'); return; }
    const tag = existing ?? normalized;
    setError('');
    onChange(withPendingTag(selected, tag));
    onInput('');
    setNotice(existing ? '已选中现有标签「' + tag + '」。' : '已添加标签「' + tag + '」，保存资料后生效。');
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
      <Button variant="secondary" isDisabled={disabled} onPress={add}>添加标签</Button>
    </div>
    <p className="media-tag-note">支持自定义标签，保存资料后生效。</p>
    {error && <p className="media-error" role="alert">{error}</p>}
    {notice && <p className="media-muted" role="status">{notice}</p>}
  </div>;
}
