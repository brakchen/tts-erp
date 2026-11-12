# tts-erp 产品方案包（按现有功能反推）

本目录是根据现有 tts-erp 前端页面、业务文档和架构师功能盘点反推的产品方案，只描述代码里已经存在的功能。交付给架构师出技术方案和产品评审使用。

整理日期：2026-11-12。依据现有前端页面、业务文档，并校准自架构师功能盘点（以 v2 代码为准）。若文档或盘点与代码冲突，以代码为准。原始功能盘点见 [appendix-feature-inventory.md](appendix-feature-inventory.md)。

## 目录

| 路径 | 说明 |
| --- | --- |
| [00-prd.md](00-prd.md) | 完整产品需求：背景、目标、角色、用户故事、范围、验收、优先级、现状缺口 |
| [01-information-architecture.md](01-information-architecture.md) | 信息架构、导航、权限矩阵、关键用户流程 |
| [pages/](pages/) | 每个运营页和辅助页一份短文档 |
| [prototypes/](prototypes/) | 可离线点击的原型（开发已按第 9 节返工提交，待测试回归、产品复验） |
| [appendix-feature-inventory.md](appendix-feature-inventory.md) | 架构师功能盘点原文（模块、权限、流程、十四条缺口） |
| [02-prototype-alignment.md](02-prototype-alignment.md) | 补需求：原型对齐现网逻辑与视觉（壳层/角色部分仍有效；第 9 节验收已被 03 取代） |
| [03-prototype-full-replica.md](03-prototype-full-replica.md) | 补需求：原型按现网逐页复刻全部页面功能与布局（开发已按第 9 节返工提交，待测试回归、产品复验） |

## 如何本地打开原型

直接用浏览器打开：

```text
prd/prototypes/index.html
```

也可以从该目录启动一个静态服务（可选，不是必须）：

```bash
cd prd/prototypes && python3 -m http.server 8765
```

然后访问 `http://127.0.0.1:8765/`。

- 原型首页可进入全部页面，也可先走登录页。
- 业务页带左侧栏（分组和文案与 `accounts/pages.py` 一致），当前页用文字导航 + 指示点高亮，无自造字母图标。桌面宽度 ≥1024px 可折叠侧栏，偏好写入 localStorage 键 `sidebar-collapsed`（`1`/`0`）；小于 1024px 使用左侧抽屉。
- 视觉对齐现网 `tokens.css`（浅绿灰纸面、白色面板、墨绿导航与浅青绿激活色），控件外观贴近工作台；令牌已复制进 `prototypes/tokens.css`，可离线打开，不外链现网构建产物或 CDN。
- 登录、新店接入授权说明、授权结果页按真实布局，无侧栏。
- 顶栏「原型角色」可在系统管理员 / 运营 / 只读分析之间切换，用于观察权限差异；只读角色下写操作按钮禁用，无权限的菜单不出现。
- 表格与卡片中的数字均为**示例数据**，不代表真实经营结果。原型不连接后端。

## 阅读建议

1. 先读 `00-prd.md` 的定位、范围和现状缺口。
2. 盈利与 ROI 口径以仓库 `docs/business/spu-profitability.md` 的 **v10** 为准，本包只引用、不重写公式。
3. 若文档、盘点与源码冲突，以源码为准，并在缺口或对应页面文档中标明差异。
4. `docs/plans/` 下尚未落地的方案不计入现有功能。
