# 页面级 Playwright E2E 测试指南

## 1. 概述

tts-erp 使用 Playwright Test 进行浏览器端到端（E2E）测试，验证页面内容、交互、状态转换等行为。

测试分为两个层级：

| 层级 | 目标 | 数据来源 | 默认执行 |
|---|---|---|---|
| **核心链路 (core)** | 真实全链路：登录 → 页面 → API → DB → 计算 → DOM | 隔离测试 DB | 页面改动时必跑 |
| **非核心链路 (extended)** | 边界/错误/视觉/并发等 | Playwright route mock + 真实页面 | 大改动/发布前 |

### 安全约束

- 永远不使用生产 `:9877` 服务作为测试目标
- 所有测试数据使用 `TEST_E2E_*` 前缀
- 使用 `TTS_ERP_AUTH_MODE=enforce` 的临时 uvicorn
- 使用真实 `/v2/auth/login` 登录获取 session cookie
- 不重算利润公式（公式由 Python API 测试覆盖）

## 2. 快速开始

### 2.1 安装依赖

```bash
npm install
npx playwright install chromium
```

### 2.2 运行测试

```bash
# SPU ROI 核心链路（日常页面改动默认执行）
bash scripts/test_e2e.sh spu-roi core

# SPU ROI 全部测试（较大重构/发布前）
bash scripts/test_e2e.sh spu-roi all

# 指定 case
bash scripts/test_e2e.sh spu-roi case C-SPUROI-07

# 按 git diff 选择受影响页面
bash scripts/test_e2e.sh changed origin/master

# 所有页面核心链路
bash scripts/test_e2e.sh all core

# 全部页面全部测试
bash scripts/test_e2e.sh all all
```

### 2.3 查看报告

```bash
npx playwright show-report
```

## 3. Case 编号与分级

### 核心链路：C-SPUROI-*

这些是 SPU ROI 页面修改后的默认必跑集合。

| 编号 | Case | 覆盖链路 |
|---|---|---|
| C-SPUROI-01 | 未登录 → 302 → 登录页 → 真实登录 → 回到原 URL | 鉴权、登录、session cookie |
| C-SPUROI-02 | 已登录加载页面；店铺/汇总/表格/CNY格式/ROI/亏损状态 | 真实 API、真实计算、DOM |
| C-SPUROI-03 | 默认日期范围按 VN 时区计算 T-1 | 时区、前端日期逻辑 |
| C-SPUROI-04 | 费率覆写、含无活动、刷新 | 控件、API 参数 |
| C-SPUROI-05 | SPU 多选 → spu_ids → URL 恢复 | TomSelect、URL 状态 |
| C-SPUROI-06 | 排序列、每页条数、翻页 | Tabulator、分页 |
| C-SPUROI-07 | 钻取面板（P&L/订单/结算/售后/广告） | drilldown API |
| C-SPUROI-08 | 店铺切换后状态重置 | 店铺选择、URL |
| C-SPUROI-09 | 退出登录后 session 失效 | logout、cookie |

### 非核心链路：N-SPUROI-*

默认不因普通页面修改而全部执行。

| 编号 | Case | 说明 |
|---|---|---|
| N-SPUROI-01 | 无店铺/URL 店铺不存在 | 店铺弹窗分支 |
| N-SPUROI-02 | 不支持 region 的时区错误 | 异常配置 |
| N-SPUROI-04 | API 500 / FX_RATE_UNAVAILABLE / 重试恢复 | 错误呈现 |
| N-SPUROI-05 | 快速切筛选旧响应不能覆盖新状态 | 并发 race |
| N-SPUROI-07 | 240+ 行、DOM 重用后 row-bad 不残留 | Tabulator 回归 |
| N-SPUROI-09 | 图片 lightbox 关闭交互 | 辅助交互 |
| N-SPUROI-11 | 移动端 viewport | 响应式 |

## 4. 选择执行策略

### 按 Git 变更选择

`suites.json` 定义了每个页面的文件关联：

```json
{
  "spu-roi": {
    "watchPaths": [
      "tts_erp_v2/static/js/spu-roi.js",
      "tts_erp_v2/api/v2/analytics.py",
      "tts_erp_v2/analytics/spu_roi.py",
      "tests/e2e/spu-roi/"
    ]
  }
}
```

`scripts/select_e2e_suites.py` 根据 `git diff` 计算受影响的 suite：

```bash
python3 scripts/select_e2e_suites.py origin/master          # core only
python3 scripts/select_e2e_suites.py origin/master --tier all
```

### 变更 → 执行映射

| 改动 | 执行范围 |
|---|---|
| `spu-roi.js` | `spu-roi core` |
| `spu-profitability-page.js` / `spu-roi.css` / `spu-profitability.html` | `spu-roi core` (+ future focused-spus) |
| `analytics/spu_roi.py` | `spu-roi core` + Python API 测试 |
| `auth.py` / `session_auth.py` / `app.py` | 所有页面 core |
| Playwright 配置/工具 | 所有页面 core |

## 5. 架构

```
scripts/
  test_e2e.sh              # 唯一浏览器测试入口
  select_e2e_suites.py     # 按 git diff 选择 suite
tests/
  e2e/
    suites.json            # 页面→文件映射
    support/               # 浏览器层 fixture（route mock 等）
    spu-roi/
      core.spec.js         # 核心全链路测试
      extended.spec.js     # 非核心 mock/边界测试
  e2e_browser/
    test_spu_roi_playwright.py  # Python 编排层
  support/
    spu_roi_e2e_seed.py    # 测试数据 seed/cleanup
playwright.config.js       # Playwright Test 配置
package.json               # npm 依赖
```

### 执行流程

```text
test_e2e.sh spu-roi core
  └─ test_isolated.sh e2e
       ├─ 克隆 tts_erp_test_template → 独立 tts_erp_test_*
       ├─ source .env.test → TTS_ERP_DB_URL_TEST
       ├─ pytest tests/e2e_browser/  （Python 编排层）
       │    ├─ seed_all(): 写入 TEST_E2E_* 数据
       │    ├─ 启动临时 uvicorn（enforce）
       │    ├─ npx playwright test --project=spu-roi --grep=@tier:core
       │    └─ cleanup(): 删除 TEST_E2E_* 数据
       └─ drop ephemeral DB
```

## 6. 添加新页面测试

1. 在 `tests/e2e/suites.json` 添加 suite 配置
2. 创建 `tests/e2e/<page-name>/core.spec.js`
3. 在 `playwright.config.js` 添加 project
4. 更新此文档