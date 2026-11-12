# 信息架构、导航、权限与关键流程

运营入口只有一套工作台 `/v2/pages/*`（服务端页面 + React 水合）。侧栏 18 页的文案、分组、顺序和可见性以 `tts_erp_v2/accounts/pages.py` 的 `PAGES` 与 `BUILTIN_ROLES` 为准。登录、授权说明和结果页是同一套前端的辅助页，无侧栏。

本方案包的默认描述范围是这套运营工作台。五款 Chrome 插件（广告、订单、拦截、监控、妙手捕获）作为已有采集或执行能力，在本文实体与流程中附件说明，不为插件自身界面单独出原型。插件在工作台已露出的部分（拦截三页、广告日明细、店铺插件发现、运行配置承载的监控策略等）仍按工作台页面描述。

## 1. 站点地图

```mermaid
flowchart TB
  subgraph public [无侧栏辅助页]
    L[登录 /v2/auth/login]
    O[新店接入授权说明 /v2/oauth/tiktok/onboard]
    M[授权或错误结果页]
    CB[TikTok 授权回调 /v2/oauth/tiktok/callback]
  end
  subgraph ws [运营工作台 /v2/pages]
    subgraph g1 [总览]
      D[控制台]
    end
    subgraph g2 [经营分析]
      F[重点关注 SPU]
      R[SPU ROI]
      P[利润劣化告警]
      A[广告日明细]
    end
    subgraph g3 [基础设置]
      C[采购工作台]
      S[店铺注册]
      E[枚举映射]
      RC[运行配置]
      J[定时任务]
      U[用户管理]
    end
    subgraph g4 [数据工具]
      IC[拦截配置]
      IR[拦截记录]
      IS[拦截统计]
      V[视频发布]
    end
    subgraph g5 [内容资产]
      MA[资产管理]
      DH[数字人]
      RH[换装视频]
    end
  end
  L -->|登录成功| D
  O -->|前往店铺注册台| S
  S -->|获取授权链接| M
  M -->|打开授权页| CB
  CB --> M
  P -->|查证| R
  F -.->|共用盈利模块| R
  MA --> DH
  DH --> RH
  RH --> V
```

没有根路径 `/` 的专用首页；日常入口是登录后的控制台。生产公网需带部署前缀（文档常见 `/tts`）。

**不要**把下列 API 能力画成现有菜单页：订单中心、售后中心、结算中心、汇率台、成本快照页、旧版利润日报页、商品实时透传页。它们只在 API 和 / 或盈利下钻中出现。

## 2. 导航结构

侧栏品牌「tts / erp」，顶栏上下文为「运营工作台 / &lt;分组&gt;」，标题为当前菜单名。可折叠侧栏。顺序即导航顺序：

| 分组 | 菜单文案 | 页面 id | 路由 |
| --- | --- | --- | --- |
| 总览 | 控制台 | dashboard | `/v2/pages/dashboard` |
| 经营分析 | 重点关注 SPU | focused-spus | `/v2/pages/focused-spus` |
| 经营分析 | SPU ROI | spu-roi | `/v2/pages/spu-roi` |
| 经营分析 | 利润劣化告警 | spu-profit-deterioration | `/v2/pages/spu-profit-deterioration` |
| 经营分析 | 广告日明细 | ad-daily | `/v2/pages/ad-daily` |
| 基础设置 | 采购工作台 | manual-costs | `/v2/pages/manual-costs` |
| 基础设置 | 店铺注册 | shops | `/v2/pages/shops` |
| 基础设置 | 枚举映射 | enum-map | `/v2/pages/enum-map` |
| 基础设置 | 运行配置 | runtime-configs | `/v2/pages/runtime-configs` |
| 基础设置 | 定时任务 | sync-jobs | `/v2/pages/sync-jobs` |
| 基础设置 | 用户管理 | users | `/v2/pages/users` |
| 数据工具 | 拦截配置 | intercept-configs | `/v2/pages/intercept-configs` |
| 数据工具 | 拦截记录 | intercept-requests | `/v2/pages/intercept-requests` |
| 数据工具 | 拦截统计 | intercept-stats | `/v2/pages/intercept-stats` |
| 数据工具 | 视频发布 | video-publish | `/v2/pages/video-publish` |
| 内容资产 | 资产管理 | media-assets | `/v2/pages/media-assets` |
| 内容资产 | 数字人 | digital-humans | `/v2/pages/digital-humans` |
| 内容资产 | 换装视频 | runninghub | `/v2/pages/runninghub` |

辅助页：

| 页面 | 路由 | 说明 |
| --- | --- | --- |
| 登录 | `/v2/auth/login` | 用户名 + 密码，无侧栏 |
| 新店接入授权说明 | `/v2/oauth/tiktok/onboard` | 跳板，引导去店铺注册台 |
| 结果说明 | 由回调等返回 | 成功 / 失败 / 授权链接，无侧栏 |
| TikTok 授权回调 | `/v2/oauth/tiktok/callback` | 卖家从 TikTok 回来的公开落点 |

## 3. 权限矩阵

权限由 **页面权限点** `page:<id>` 与 **API 档位**（readonly &lt; readwrite &lt; admin）共同决定。能进页面通常即可使用该页允许的功能，但若干接口另有档位要求。前端写按钮只是提示，真正放行以服务端为准。

### 3.1 预置角色 × 页面

| 页面 | 系统管理员 | 运营 | 只读分析 | 打开页面的额外档位 | 写操作要点 |
| --- | --- | --- | --- | --- | --- |
| 控制台 | 是 | 是 | 是 | — | 只读 |
| 重点关注 SPU | 是 | 是 | 是 | — | 改关注清单需 readwrite |
| SPU ROI | 是 | 是 | 是 | — | 只读（临时费率是查询参数） |
| 利润劣化告警 | 是 | 是 | **否** | — | 只读对比 |
| 广告日明细 | 是 | 是 | 是 | — | 只读 |
| 采购工作台 | 是 | 是 | 否 | — | 写入需 readwrite |
| 店铺注册 | 是 | 是 | 否 | — | 配置 / 授权链接需 readwrite |
| 枚举映射 | 是 | 是 | 否 | GET 可读 | **增删改需 admin**（登记表误写 readwrite，以 handler 为准） |
| 运行配置 | 是 | 是 | 否 | **打开即要求 readwrite** | 发布 / 凭证需 write |
| 定时任务 | 是 | 是 | 否 | **打开即要求 readwrite** | 手动触发 = readwrite；启停周期 = admin |
| 用户管理 | 是 | **否** | 否 | 绑定 page:users | 维护需 admin |
| 拦截配置 | 是 | 是 | 否 | **含 GET/导出均需 readwrite** | 维护规则 |
| 拦截记录 | 是 | 是 | 否 | 见策略；预置只读无此页 | 以查询详情为主 |
| 拦截统计 | 是 | 是 | 是 | — | 只读 |
| 视频发布 | 是 | **否**（需另授 page:video-publish） | 否 | 绑定页面权限 | 会发到 TikTok 内容侧 |
| 资产管理 | 是 | 是 | 否 | — | 写入需 readwrite |
| 数字人 | 是 | 是 | 否 | — | 写入需 readwrite |
| 换装视频 | 是 | 是 | 否 | 绑定 page:runninghub | 生成计费；工作流 / 账户类需 admin |

浏览器会话优先级：有效 cookie &gt; Bearer API key &gt; X-API-Key。API key 没有页面权限，供插件和脚本使用。工作台没有签发、轮换或吊销密钥的页面，实际通过命令行 `api_keys.py` 管理；完整密钥只在创建或轮换时打印一次。

### 3.2 权限概念关系

```mermaid
flowchart LR
  U[用户] --> UR[用户-角色]
  UR --> R[角色]
  R --> T[API 档位<br/>readonly / readwrite / admin]
  R --> P[页面权限点 page:id]
  P --> NAV[侧栏可见页面]
  T --> API[数据接口放行]
```

## 4. 核心实体（产品视角）

店铺是分析主范围。其下有商品 SPU、订单与订单行、履约、售后、结算、广告日明细、重点关注清单和平台费率估算。采购侧是人工成本、妙手货源价和成本快照。汇率快照是盈利换算的必备依据。

插件落库的 Chrome 订单域表与官方同步进入的 commerce / fulfillment / after_sales / finance 是两套来源，共同服务分析，**不**在工作台呈现为两套订单中心。

内容侧：媒体资产 → 素材包 → 数字人档案 → 换装批次；以及独立的视频发布任务与设备。

账号侧：用户、角色、会话；另有独立 API key 与运行配置 / 加密凭证、枚举映射、同步任务记录。

## 5. 关键用户流程

### 5.1 登录进入工作台

```mermaid
sequenceDiagram
  actor 用户
  participant 登录页
  participant 会话
  participant 控制台
  用户->>登录页: 输入用户名和密码
  登录页->>会话: 校验（不开放注册）
  alt 成功
    会话-->>用户: 写入 HttpOnly cookie
    登录页->>控制台: 跳转 next 或默认运营页
  else 失败或未确认
    登录页-->>用户: 统一错误或请查询当前会话
  end
```

工作台顶栏可退出。改自己的密码目前只能走 API，页面无入口。

### 5.2 新店从发现到授权上线

```mermaid
flowchart TD
  A[插件在 Seller Center 登记店铺<br/>或工作台手工补录] --> B[店铺注册台核对资料]
  B --> C[readwrite 配置 Partner App<br/>Service ID + Key/Secret]
  C --> D[获取一次性授权链接]
  D --> E[用目标店卖家账号打开链接]
  E --> F[TikTok 回调交换令牌]
  F --> G[官方 tiktok.* 与 token.refresh<br/>按已授权店扇出]
  G --> H[控制台 / 定时任务查看运行结果]
  A -.->|插件上报不依赖授权| I[广告或监控等数据可先入库]
```

`/v2/oauth/tiktok/onboard` 只解释上述路径并跳回店铺注册台，不是可完成授权的向导。

### 5.3 日常看经营与 SPU 盈利

```mermaid
flowchart TD
  A[控制台看覆盖与同步健康] --> B{看哪些商品}
  B -->|全量或临时指定| C[SPU ROI]
  B -->|该店关注清单| D[重点关注 SPU]
  C --> E[选店和经营日期 默认昨天]
  D --> E
  E --> F[看大盘当前 / 预计最终 与明细]
  F --> G[下钻订单 物流 结算 售后 广告]
  F --> H[怀疑变差则打开利润劣化对比]
  H --> C
  F --> I[需要原始投放则打开广告日明细]
  F -.->|缺成本| J[采购工作台补人工价]
  E -.->|无汇率快照| X[整页不可算]
```

### 5.4 人工维护采购成本

```mermaid
flowchart TD
  A[打开采购工作台并选店] --> B[常用：缺成本候选]
  B --> C[核对货源价与主图]
  C --> D[编辑草稿：成本 币种 备注 生效时间]
  D --> E[确认逐行提交或提交本页已编辑行]
  E --> F[最近提交中核对历史]
  F --> G[下次盈利计算优先使用人工价]
```

### 5.5 查看与干预同步

```mermaid
flowchart TD
  A[控制台或定时任务页] --> B[查看上次运行 落后周期 严重级别]
  B --> C{需要补跑?}
  C -->|readwrite 立即执行| D[可指定单店或全部已授权店<br/>全店铺二次确认]
  C -->|admin 停用周期| E[自动调度暂停 手动触发仍可用]
```

当前已注册的主要同步任务（产品认知，周期以调度注册表为准）：TikTok 订单 / 详情 / 商品 / 物流 / 售后 / 财务、凭证续期、汇率快照、妙手店铺与采集箱 / 货源成本 / 包裹、成本快照、旧版利润日报、SPU 主图镜像、店铺平台费率估算。已停调度的 today→daily、利润劣化每日评估器不再作为现行任务。

### 5.6 内容生产到发布（可选）

```mermaid
flowchart LR
  A[资产管理：上传图/视频 组包] --> B[数字人档案：形象与素材包]
  B --> C[换装视频：选服装与模板]
  C --> D[确认 RunningHub 费用后出片]
  D --> E[视频发布：入队 设备执行 核验]
  E --> F[发布到 TikTok 内容侧]
```

各步受页面权限和读写档位约束。预置运营默认不能进视频发布页。监控插件按规则在 Seller Center 会话关广告计划，属于另一条外部写路径，不经过本流程。

### 5.7 权限治理

管理员在用户管理中建账号、分配预置或自定义角色、必要时重置密码或吊销会话。用户自行登录。改自己的密码目前没有工作台入口。
