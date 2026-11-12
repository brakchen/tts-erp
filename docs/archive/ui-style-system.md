> 归档说明：本文仍描述 2026-10-03 暖纸编辑体色板。现行色板以 	ts_erp_v2/static/css/tokens.css 为准。本文第 4、7、8 节含 2026-11-12 对 React/Vite 接入方式的更新，一并归档。

# UI 设计体系（暖纸编辑体）

> 本页记录共享样式令牌。页面与组件接入见 [前端开发规范](../guides/frontend-development.md)。
> React 样式入口是 [frontend/src/styles.css](../../frontend/src/styles.css)。
> 旧模板的组件类和映射保留在本文供维护历史资源时参考。

## 1. 背景：重构前有 4 套并行的令牌体系

11 个页面各自维护一份 `:root`，衍生出 4 套命名体系与 3 种圆角规范、2 套字体栈、
2 套强调色。同一个语义在不同页面叫不同名字、取不同值：

| 体系 | 令牌命名 | 强调色 | 圆角 | 用在 |
| --- | --- | --- | --- | --- |
| 暖纸编辑体 | `--paper` / `--ink` / `--rule` / `--accent` | 陶土橙 `#B8390E` | 0 | dashboard、intercept-\*、manual-costs、shops |
| 灰蓝体系 | `--bg` / `--card` / `--border` / `--text` | 靛蓝 `#5b6abf` | 3–4px | enum-map、users |
| `--rc-*` 命名空间 | `--rc-paper` / `--rc-ink` / `--rc-accent` | 青绿 `#0e6b70` | 3px | runtime-configs |
| `--signal*` 体系 | `--signal` / `--signal-bright` / `--warning` | 青蓝 `#006e90` | 0 | ad-daily |

统一后：**全站只有暖纸编辑体一套令牌**，其余三套映射进来。

## 2. 设计令牌（`tokens.css`）

`tokens.css` 是**唯一的 `:root` 来源**。页面模板与页面级 CSS 不得再定义 `:root` 令牌。

### 纸面（背景层次）

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `--paper` | `#F4EFE4` | 页面底色 |
| `--paper-deep` | `#EAE3D2` | 下沉/悬停面、表头底色 |

### 墨（文字层次）

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `--ink` | `#1B1814` | 正文 |
| `--ink-soft` | `#4A4239` | 次级正文 |
| `--muted` | `#6E6657` | 弱化说明、eyebrow、表头文字 |

### 界线（分割线 / 描边）

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `--rule` | `#C9BFA8` | 主分割线、按钮描边 |
| `--rule-soft` | `#DDD4BF` | 行分割线、弱描边 |

### 语义色

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `--accent` | `#B8390E` | 强调色（陶土橙），链接、hover、主按钮 hover |
| `--accent-deep` | `#8F2C09` | 强调色深阶，hover/pressed |
| `--ok` | `#2F6B3E` | 成功 |
| `--warn` | `#8A6D1A` | 警告 |
| `--danger` | `#8C1A1A` | 危险 |

### 字体

| 令牌 | 栈 |
| --- | --- |
| `--mono` | `'JetBrains Mono', 'SF Mono', 'Cascadia Mono', 'Fira Code', Consolas, 'Liberation Mono', ui-monospace, 'Noto Sans Mono CJK SC', monospace` |
| `--sans` | `'Inter', 'Noto Sans SC', 'Noto Sans CJK SC', 'Source Han Sans SC', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, -apple-system, 'Segoe UI', sans-serif` |
| `--serif` | `'Noto Serif SC', 'Noto Serif CJK SC', 'Source Han Serif SC', 'Iowan Old Style', 'Apple Garamond', Georgia, ui-serif, serif` |

> **中英双族名是必需的**：同一款字体有两个家族名——Google 命名（`Noto Sans SC`）
> 与发行版命名（`Noto Sans CJK SC`）。Debian/Ubuntu 只装后者，缺了它中文只能靠
> 浏览器系统回退，可能掉到 `Droid Sans Fallback` 这类异族字体，出现「同一页中英文
> 不是一套字」的观感。
>
> 全站只允许 `var(--mono|sans|serif)` 三种字体来源；页面级 CSS、内联 `<style>`、
> JS 注入样式、vendor 组件（jsoneditor / Ace）一律不得自写 `font-family`。校验方式：
> `scripts/probe_ui_font_audit.js` 用 CDP `getPlatformFontsForNode` 实测各页真正命中
> 的字体族（2026-10-04 `feature/ui-font-unify` lane 首次巡检：12 页此前命中 7 套栈，
> `runtime-configs` 的 vendor 编辑器掉到 `DejaVu Sans Mono` / `Liberation Sans`，
> 修后 12 页收敛为同一套）。

### 形状

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `--radius` | `0` | 全站圆角基准。暖纸编辑体是**直角**体系 |

> 需要圆形/胶囊（头像、状态点）时用 `50%` / `999px`，那是形状不是圆角装饰，
> 不受 `--radius` 约束。

## 3. 公共组件（`common.css`）

页面模板**不得再内联这些规则**。规范值见 `common.css`，此处只列清单：

- **基础层**：`*`、`html, body`、`a`、`a:hover`、`.mono`
- **页头**：`.op-header`、`.op-header-row`、`.op-header-titles`、`.op-eyebrow`、
  `.op-title`、`.op-identity`、`.op-home-link`、`.op-home-link:hover`
- **容器**：`.page-main`、`.section-title`
- **表格**：`.op-table`、`.op-th`、`.op-table td`、`.op-table tbody tr:hover`、
  `.op-empty`、`.op-error`
- **工具条**：`.toolbar`、`.toolbar-field`、`.toolbar-spacer`、`.actions`
- **徽标**：`.badge`、`.badge-ok`、`.badge-api`、`.badge-plugin`
- **按钮**：`.btn-primary`、`.btn-secondary`、`.btn-icon`（含 `:hover` / `:disabled`）
- **响应式**：`@media (max-width: 720px)` 下的工具条与页头/容器收窄

### 组件规范值的取舍

重构前这些规则在 6～9 个页面里各存一份，且有 1～3px 漂移。统一为多数投票值，
再按暖纸编辑体旗舰页 `dashboard.html` 校正：

| 规则 | 各页取值 | 定稿 |
| --- | --- | --- |
| `.op-eyebrow` | 11 / 12 / 13px | 13px |
| `.op-title` | 22 / 24 / 26px | 26px |
| `.op-identity` | 13 / 14px | 14px |
| `.op-table` | 13 / 14px | 14px |
| `.op-th` | 10 / 11 / 12px | 12px |
| `.op-table td` | 10 / 14px | 14px |
| `.badge` | 10 / 11px | 11px |
| `html, body` | 14 / 15 / 16px | 16px |
| `.btn-primary` | 墨底纸字 / accent 底 | 墨底纸字（hover 转 accent） |

密集页面如果确实需要更小字号，**覆盖具体规则的 `font-size`**，不要复制一份
`html, body` 规则。

## 4. 页面接入方式

生产前端使用 React、TypeScript、Vite 和 HeroUI。服务端由
[`render_frontend_page`](../../tts_erp_v2/api/v2/frontend_pages.py) 注入 bootstrap 并按 manifest
加载产物。React 入口 [main.tsx](../../frontend/src/main.tsx) 将业务页面渲染到
[AppShell](../../frontend/src/app/AppShell.tsx)，复用导航、页头和页面容器。

全局样式从 `frontend/src/styles.css` 引入 Tailwind、HeroUI、共享令牌及导航样式。
页面模块可以引入局部 CSS，复用同一套令牌。Vite 构建处理静态资产版本，页面 URL
用共享 `pathFor` 从 bootstrap 的 `rootPath` 生成。

## 5. 重构前的令牌映射表

| 旧令牌 | 新令牌 | 说明 |
| --- | --- | --- |
| `--bg` | `var(--paper)` | 灰蓝体系页面底色 |
| `--card` | `var(--paper)` | 白卡片 → 纸面（暖纸编辑体无纯白卡片） |
| `--border` | `var(--rule-soft)` | |
| `--text` | `var(--ink)` | |
| `--signal` | `var(--accent)` | **青蓝 → 陶土橙，有意的品牌统一** |
| `--signal-bright` | `var(--accent-deep)` | |
| `--warning` | `var(--warn)` | |
| `--rc-paper` | `var(--paper)` | `--rc-*` 命名空间整体去掉前缀 |
| `--rc-panel` | `var(--paper-deep)` | |
| `--rc-accent` | `var(--accent)` | 青绿 → 陶土橙 |

surface 色（淡强调底、亮/暗纸面）在 `tokens.css` 里**没有对应项**，不要为它们
新增并行色板；用 `color-mix()` 从规范令牌派生：

```css
--signal-pale: color-mix(in srgb, var(--accent) 16%, var(--paper));
```

## 6. 有意**不**合并的同名规则

这些规则在不同页面同名但语义不同，合并会改行为，因此保留在各页面：

| 规则 | 分歧 | 结论 |
| --- | --- | --- |
| `.status-ok` / `.status-err` | dashboard 是**填充色块**（`background`），intercept-requests 是**文字色**（`color`） | 两个不同组件，不合并 |
| `.wrap` / `.header` | enum-map 与 users 的布局容器，宽度与用途都不同 | 页面私有 |
| `.actions` | intercept-configs 是 `nowrap` 表格单元格，其余是 flex 容器 | 语义不同 |

## 7. 新增页面检查

- [ ] 在页面模块和加载器注册入口，复用 AppShell 和 HeroUI 组件。
- [ ] 使用共享令牌与组件样式。页面样式限于该页的布局和业务状态。
- [ ] 验证 `rootPath` 下的页面、资源加载、键盘操作和窄屏布局。

## 8. 旧侧边栏令牌回退

`tts_erp_v2/api/v2/pages.py` 中的旧侧边栏 CSS 保留兼容回退链。
新增 React 页面按 §4 使用 AppShell。
`tests/api/test_pages.py::test_sidebar_tokens_fall_back_on_pages_with_a_different_theme_vocabulary`
锁定了这条性质。
