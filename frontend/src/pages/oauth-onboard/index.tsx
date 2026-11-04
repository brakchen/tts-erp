import { Alert, Card, buttonVariants } from '@heroui/react';
import { pathFor } from '../../shared/api/paths';
import type { PageProps } from '../../shared/contracts';

/** Reuse the shop workbench's shop-bound authorization and recovery owner. */
export default function OAuthOnboardPage({ bootstrap }: PageProps) {
  return <div className="mx-auto w-full max-w-3xl space-y-5 p-6 sm:p-10">
    <Card><Card.Header><Card.Title render={(props) => <h2 {...props} />}>新店接入授权</Card.Title>
      <Card.Description>选择目标店铺，完成卖家授权</Card.Description></Card.Header>
      <Card.Content><div className="space-y-4">
        <p>先在店铺注册台选择店铺、配置 App，再获取该店铺的授权链接。</p>
        <ol className="list-decimal space-y-2 pl-6">
          <li>在店铺注册台选择目标店铺并配置 App。</li>
          <li>获取授权链接，使用该店铺的卖家账号打开授权页面。</li>
          <li>完成 TikTok 授权后，回调页面会显示结果；同步任务在下次运行时接管。</li>
        </ol>
        <p className="break-all">Partner Center 回调地址：<code>{pathFor(bootstrap.rootPath, '/v2/oauth/tiktok/callback')}</code></p>
        <a href={pathFor(bootstrap.rootPath, '/v2/pages/shops')} className={buttonVariants()}>前往店铺注册台</a>
      </div></Card.Content>
    </Card>
    <Alert status="default"><Alert.Content><Alert.Title>权限与有效期</Alert.Title>
      <Alert.Description>只读账号可查看说明；生成链接需要 readwrite 及以上权限和店铺页面访问权限。授权链接单次有效，过期或已使用后请重新获取。</Alert.Description>
    </Alert.Content></Alert>
  </div>;
}
