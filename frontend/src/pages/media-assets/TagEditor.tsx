import { Button, Input, Label, Tag, TagGroup, TextField } from '@heroui/react';
import { decodeTags } from './contracts';
import { ReadStatus, useMediaRead } from './media-ui';

/** Each input is one tag, including punctuation. Candidates stay server-owned. */
export function withPendingTag(selected: readonly string[], input: string): string[] {
  return [...new Set([...selected, input].map((tag) => tag.trim()).filter(Boolean))];
}

export default function TagEditor({ root, selected, input, onChange, onInput, disabled = false }: {
  root: string; selected: string[]; input: string; onChange: (tags: string[]) => void;
  onInput: (value: string) => void; disabled?: boolean;
}) {
  const read = useMediaRead(root, '/assets/tags', decodeTags);
  const choices = [...new Set([...(read.data ?? []), ...selected])];
  function add() {
    if (!input.trim() || disabled) return;
    onChange(withPendingTag(selected, input));
    onInput('');
  }
  return <div className="space-y-3">
    <TagGroup aria-label="选择素材标签" selectionMode="multiple" selectedKeys={new Set(selected)}
      disabledKeys={disabled ? choices : []}
      onSelectionChange={(keys) => onChange(keys === 'all' ? choices : Array.from(keys, String))}>
      <Label>标签（可多选）</Label>
      <TagGroup.List renderEmptyState={() => <p className="text-sm text-muted">暂无标签，可在下方新增。</p>}>
        {choices.map((tag) => <Tag key={tag} id={tag} textValue={tag} className="max-w-full [overflow-wrap:anywhere]">{tag}</Tag>)}
      </TagGroup.List>
    </TagGroup>
    <ReadStatus title="已有标签" loading={read.loading} error={read.error} onRetry={read.retry} />
    <div className="flex flex-wrap items-end gap-2">
      <TextField value={input} onChange={onInput} isDisabled={disabled} className="min-w-0 flex-1">
        <Label>新增标签</Label><Input maxLength={64} placeholder="输入一个标签，按 Enter 添加" onKeyDown={(event) => {
          if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); if (!event.nativeEvent.isComposing) add(); }
        }} />
      </TextField>
      <Button variant="secondary" isDisabled={disabled || !input.trim()} onPress={add}>添加标签</Button>
    </div>
    <p className="text-sm text-muted">新增标签会自动选中，保存或上传后才进入素材库的标签候选。标签可包含逗号。</p>
  </div>;
}
