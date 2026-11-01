import { Button, Table } from '@heroui/react';
import type { Item } from './contracts';
export default function ConfigList({ items, disabled, open }: { items: Item[]; disabled: boolean; open: (item: Item) => void }) {
  return <Table><Table.ScrollContainer role="region" aria-label="运行配置列表"><Table.Content aria-label="运行配置" className="min-w-[40rem]">
    <Table.Header><Table.Column isRowHeader>配置</Table.Column><Table.Column>生效状态</Table.Column><Table.Column>草稿状态</Table.Column><Table.Column>更新时间</Table.Column><Table.Column>操作</Table.Column></Table.Header>
    <Table.Body renderEmptyState={() => '暂无配置，可通过“新建配置”创建'}>{items.map((item) => <Table.Row key={item.configKey} id={item.configKey}>
      <Table.Cell><span className="block font-semibold break-all">{item.displayName}</span><code className="block text-muted break-all">{item.configKey}</code></Table.Cell>
      <Table.Cell>{item.retiredAt ? '已停用' : item.publishedVersion === null ? '尚未发布' : `已发布 v${item.publishedVersion}`}</Table.Cell>
      <Table.Cell>{item.hasDraft ? '有已保存草稿' : '无草稿'}</Table.Cell><Table.Cell>{item.updatedAt}</Table.Cell>
      <Table.Cell focusMode="child"><Button size="sm" variant="secondary" aria-label={`查看与修改 ${item.displayName}`} isDisabled={disabled} onPress={() => open(item)}>查看与修改</Button></Table.Cell>
    </Table.Row>)}</Table.Body>
  </Table.Content></Table.ScrollContainer></Table>;
}
