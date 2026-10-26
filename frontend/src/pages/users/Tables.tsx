import { Button, Table } from '@heroui/react';
import type { Catalog, Role, Session, User } from './contracts';

export type UserAction = 'detail' | 'sessions' | 'displayName' | 'roles' | 'password' | 'status';
export const shown = (value: string | null) => value === null || value === '' ? '—' : value;

export function UsersTable({ rows, filtered, canManage, blocked, onAction }: {
  rows: User[]; filtered: boolean; canManage: boolean; blocked: boolean;
  onAction: (action: UserAction, user: User) => void;
}) {
  return <Table>
    <Table.ScrollContainer role="region" aria-label="用户表格">
      <Table.Content aria-label="用户列表" className="min-w-[65rem]">
        <Table.Header>
          <Table.Column isRowHeader>用户名</Table.Column>
          <Table.Column>显示名</Table.Column>
          <Table.Column>状态</Table.Column>
          <Table.Column>角色</Table.Column>
          <Table.Column>最后登录</Table.Column>
          <Table.Column>活跃会话数</Table.Column>
          <Table.Column>操作</Table.Column>
        </Table.Header>
        <Table.Body renderEmptyState={() => filtered ? '无匹配用户' : '暂无用户'}>
          {rows.map((user) => {
            const actions: [UserAction, string][] = [
              ['displayName', '编辑显示名'], ['roles', '分配角色'],
              ['password', '重置密码'], ['status', user.status === 'active' ? '停用' : '启用'],
            ];
            return <Table.Row id={user.id} key={user.id}>
              <Table.Cell><span className="block w-36 break-all">{user.username}</span></Table.Cell>
              <Table.Cell><span className="block w-40 break-all">{user.displayName}</span></Table.Cell>
              <Table.Cell>{user.status === 'active' ? '启用' : user.status === 'disabled' ? '停用' : user.status}</Table.Cell>
              <Table.Cell><span className="block w-36 break-all">{user.roles.join('、') || '—'}</span></Table.Cell>
              <Table.Cell>{shown(user.lastLoginAt)}</Table.Cell>
              <Table.Cell>{user.activeSessions}</Table.Cell>
              <Table.Cell focusMode="child">
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" isDisabled={blocked} onPress={() => onAction('detail', user)}>详情</Button>
                  <Button size="sm" isDisabled={blocked} onPress={() => onAction('sessions', user)}>会话</Button>
                  {canManage && actions.map(([kind, label]) => <Button size="sm" key={kind}
                    isDisabled={blocked} onPress={() => onAction(kind, user)}>{label}</Button>)}
                </div>
              </Table.Cell>
            </Table.Row>;
          })}
        </Table.Body>
      </Table.Content>
    </Table.ScrollContainer>
  </Table>;
}

export function RolesTable({ catalog, canManage, blocked, onAction }: {
  catalog: Catalog; canManage: boolean; blocked: boolean;
  onAction: (kind: 'editRole' | 'deleteRole', role: Role) => void;
}) {
  return <Table>
    <Table.ScrollContainer role="region" aria-label="角色表格">
      <Table.Content aria-label="角色列表" className="min-w-[54rem]">
        <Table.Header>
          <Table.Column isRowHeader>角色代码</Table.Column>
          <Table.Column>角色名称</Table.Column>
          <Table.Column>描述</Table.Column>
          <Table.Column>API 档位</Table.Column>
          <Table.Column>类型</Table.Column>
          <Table.Column>权限点</Table.Column>
          <Table.Column>用户数</Table.Column>
          <Table.Column>操作</Table.Column>
        </Table.Header>
        <Table.Body renderEmptyState={() => '暂无角色'}>
          {catalog.roles.map((role) => <Table.Row id={role.code} key={role.code}>
            <Table.Cell>{role.code}</Table.Cell>
            <Table.Cell>{role.name}</Table.Cell>
            <Table.Cell><span className="block w-40 break-all">{shown(role.description)}</span></Table.Cell>
            <Table.Cell>{role.apiTier}</Table.Cell>
            <Table.Cell>{role.isBuiltin ? '内置' : '自定义'}</Table.Cell>
            <Table.Cell><span className="block w-56 break-all">{role.permissions.map((code) =>
              catalog.allPermissions.find((p) => p.code === code)?.label ?? code).join('、') || '—'}</span></Table.Cell>
            <Table.Cell>{role.userCount}</Table.Cell>
            <Table.Cell focusMode="child"><div className="flex flex-wrap gap-2">
              <Button size="sm" isDisabled={!canManage || blocked || role.isBuiltin && role.code === 'admin'}
                onPress={() => onAction('editRole', role)}>编辑角色</Button>
              <Button size="sm" variant="danger" isDisabled={!canManage || blocked || role.isBuiltin}
                onPress={() => onAction('deleteRole', role)}>删除角色</Button>
            </div></Table.Cell>
          </Table.Row>)}
        </Table.Body>
      </Table.Content>
    </Table.ScrollContainer>
  </Table>;
}

export function SessionsTable({ rows, blocked, canManage, onRevoke }: {
  rows: Session[]; blocked: boolean; canManage: boolean; onRevoke: (id: string) => void;
}) {
  return <Table>
    <Table.ScrollContainer role="region" aria-label="会话表格">
      <Table.Content aria-label="用户会话" className="min-w-[65rem]">
        <Table.Header>
          <Table.Column isRowHeader>会话 ID</Table.Column>
          <Table.Column>创建时间</Table.Column>
          <Table.Column>到期时间</Table.Column>
          <Table.Column>IP</Table.Column>
          <Table.Column>User-Agent</Table.Column>
          <Table.Column>最后活跃</Table.Column>
          <Table.Column>吊销时间</Table.Column>
          <Table.Column>状态</Table.Column>
          <Table.Column>操作</Table.Column>
        </Table.Header>
        <Table.Body renderEmptyState={() => '该用户暂无会话'}>
          {rows.map((session) => <Table.Row id={session.id} key={session.id}>
            <Table.Cell>{session.id}</Table.Cell>
            <Table.Cell>{session.createdAt}</Table.Cell>
            <Table.Cell>{session.expiresAt}</Table.Cell>
            <Table.Cell>{shown(session.ip)}</Table.Cell>
            <Table.Cell><span className="block w-48 break-all">{shown(session.userAgent)}</span></Table.Cell>
            <Table.Cell>{shown(session.lastSeenAt)}</Table.Cell>
            <Table.Cell>{shown(session.revokedAt)}</Table.Cell>
            <Table.Cell>{session.revokedAt !== null ? '已吊销' : session.active ? '活跃' : '已过期'}</Table.Cell>
            <Table.Cell focusMode="child"><Button size="sm" variant="danger"
              isDisabled={!canManage || blocked || !session.active} onPress={() => onRevoke(session.id)}>吊销此会话</Button></Table.Cell>
          </Table.Row>)}
        </Table.Body>
      </Table.Content>
    </Table.ScrollContainer>
  </Table>;
}
