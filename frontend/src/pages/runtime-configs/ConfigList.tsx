import { Button, Table } from '@heroui/react';
import type { Item } from './contracts';
export default function ConfigList({ items, disabled, open }: { items: Item[]; disabled: boolean; open: (item: Item) => void }) {
  return <Table variant="secondary" className="erp-table"><Table.ScrollContainer className="erp-table-scroll" tabIndex={0} role="region" aria-label="运行配置列表"><Table.Content aria-label="运行配置" className="min-w-[40rem] erp-table-content">
    <Table.Header className="erp-table-head"><Table.Column className="erp-table-column" isRowHeader>配置</Table.Column><Table.Column className="erp-table-column">生效状态</Table.Column><Table.Column className="erp-table-column">草稿状态</Table.Column><Table.Column className="erp-table-column">更新时间</Table.Column><Table.Column className="erp-table-column">操作</Table.Column></Table.Header>
    <Table.Body className="erp-table-body" renderEmptyState={() => <div className="erp-table-empty">{'暂无配置，可通过“新建配置”创建'}</div>}>{items.map((item) => <Table.Row className="erp-table-row" key={item.configKey} id={item.configKey}>
      <Table.Cell className="erp-table-cell"><span className="block font-semibold break-all">{item.displayName}</span><code className="block text-muted break-all">{item.configKey}</code></Table.Cell>
      <Table.Cell className="erp-table-cell">{item.retiredAt ? '已停用' : item.publishedVersion === null ? '尚未发布' : `已发布 v${item.publishedVersion}`}</Table.Cell>
      <Table.Cell className="erp-table-cell">{item.hasDraft ? '有已保存草稿' : '无草稿'}</Table.Cell><Table.Cell className="erp-table-cell">{item.updatedAt}</Table.Cell>
      <Table.Cell className="erp-table-cell" focusMode="child"><Button className="erp-table-action" size="sm" variant="secondary" aria-label={`查看与修改 ${item.displayName}`} isDisabled={disabled} onPress={() => open(item)}>查看与修改</Button></Table.Cell>
    </Table.Row>)}</Table.Body>
  </Table.Content></Table.ScrollContainer></Table>;
}
