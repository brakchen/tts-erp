import { Alert, Card, Table, buttonVariants } from '@heroui/react';
import { pathFor } from '../../shared/api/paths';
import type { PageProps } from '../../shared/contracts';

const actions = {
  shops: { path: '/v2/pages/shops', label: '前往店铺注册台' },
  login: { path: '/v2/auth/login', label: '返回登录页' },
  onboard: { path: '/v2/oauth/tiktok/onboard', label: '重新开始授权' },
};

/** Completed server result only: no session probe, command, or callback replay. */
export default function MessagePage({ bootstrap }: PageProps) {
  const data = bootstrap.pageData;
  if (data?.kind !== 'message') throw new Error('缺少结果页面数据');
  const action = actions[data.action];
  return <div className="mx-auto w-full max-w-3xl space-y-5 p-6 sm:p-10">
    <Alert status={data.tone}><Alert.Content>
      <Alert.Title render={(props) => <h2 {...props} />}>{data.title}</Alert.Title>
      <Alert.Description>{data.message}</Alert.Description>
    </Alert.Content></Alert>
    {data.authorization && <Card><Card.Header><Card.Title>授权链接</Card.Title></Card.Header><Card.Content>
      <p className="break-words">链接有效至 {data.authorization.expiresAt}，仅可使用一次。</p>
      <p className="break-all"><code>{data.authorization.url}</code></p>
      <div><a href={data.authorization.url} target="_blank" rel="noopener noreferrer" className={buttonVariants()}>打开授权页面</a></div>
    </Card.Content></Card>}
    {data.sections.map((section, sectionIndex) => <Card key={sectionIndex}>
      <Card.Header><Card.Title>{section.title}</Card.Title></Card.Header>
      <Card.Content><Table>
        <Table.ScrollContainer tabIndex={0} role="region" aria-label={section.title}>
          <Table.Content aria-label={`${section.title}授权结果`}>
            <Table.Header><Table.Column isRowHeader className="min-w-20">项目</Table.Column><Table.Column>内容</Table.Column></Table.Header>
            <Table.Body>{section.facts.map((fact, factIndex) => <Table.Row key={factIndex} id={String(factIndex)}>
              <Table.Cell className="min-w-20">{fact.label}</Table.Cell><Table.Cell><span className="break-all">{fact.value}</span></Table.Cell>
            </Table.Row>)}</Table.Body>
          </Table.Content>
        </Table.ScrollContainer>
      </Table></Card.Content>
    </Card>)}
    <a href={pathFor(bootstrap.rootPath, action.path)} className={buttonVariants({ variant: 'secondary' })}>{action.label}</a>
  </div>;
}
