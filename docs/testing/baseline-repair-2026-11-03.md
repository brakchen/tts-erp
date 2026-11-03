# 基线失败修复（2026-11-03）

本任务从 `origin/master b3544d1f` 建立独立工作树，修复
[11 月 1 日调查](baseline-failure-investigation-2026-11-01.md)记录的剩余失败。
已提交的 `baseline-fast.txt` 仍要求零失败，不通过更新基线接纳失败。
主工作树的未跟踪 `uv.lock`、虚拟机主树的备份脚本修改及其他分支均不在写域。

## 修复边界

- 商业 API 测试复用 `authorize_test_shop` 建立本店 App、Service、credential
  与版本的真实关联；继续验证原金额、分页、事务、空样本及异常结果。
  已授权空店铺保留零统计，未知店铺按现行合同返回 404。
- OAuth 测试使用目标店铺及 App revision 绑定的 state，验证单目标回调；
  多店返回必须拒绝且不创建额外凭据。管理读取显式使用 management view。
  店铺名称/地区只读，CSRF 测试使用合法日期请求以真正进入 handler。
- 浏览器测试覆盖现行补录、日期编辑、独立 App 配置、登录与成本编辑流程；
  request fence 只接受固定管理列表和独立读回核验的店铺主键，继续验证真实
  422/409、角色、CSRF、密钥草稿和提交丢响应后不自动重放。
- Tailwind 字体映射回 canonical `--sans`、`--serif`、`--mono`，避免
  `font-mono` 及 preflight 的 code/pre 引入第四套栈。使用既有 Tailwind
  `@theme inline` 机制，无新依赖，依据为
  [官方字体文档](https://tailwindcss.com/docs/font-family#customizing-your-theme)。
- 店铺列表 effect 曾把只用于按钮状态的 `readBlocked` 当作请求触发依赖，
  导致 Retry-After 到期自动 GET。删除这个未被 effect 读取的依赖，保留
  `readNotBefore` 时间门禁、手动刷新、筛选、翻页与焦点触发；既有组件测试
  新增到期零读取及显式刷新一次的断言。相邻页面未发现同样依赖。
- 店铺筛选表单恢复复用共享 `erp-filters` 的按内容收宽行为，保留本页 grid
  和现行筛选字段；原 2560px 下 `<1100px` 验收有已批准的
  [紧凑布局要求](../plans/compact-page-layouts-2026-11-02.md)依据，保持不变。
- 图表测试从 canonical `--accent` 取样，继续检查实际像素、非零柱数量与等宽。
  令牌检查复用 tinycss2 收集组件作用域属性名；root palette 检查仍限定 `:root`，
  此静态名称检查不宣称模拟 CSS 继承或级联。
- DDL 通过现有 `regen_schema.py` 从已确认在 `0072_shop_app_credentials` 的
  测试模板只读生成。历史 0070 脚本的 probe 测试检查实际当前 Alembic head；
  脚本本身的 0069/0070 执行边界保持。

以上必要性为旧测试先于业务断言被新授权/请求合同拦截，以及共享字体出口存在
真实旁路。修复位于对应测试消费者和共享字体入口，不增加授权实现或兼容回退。

## 验证

在指定测试虚拟机 `schan@192.168.47.129` 的独立工作树
`/tmp/tts-erp-baseline-repair-vm` 执行。所有测试通过
`bash scripts/test_isolated.sh ...`；每轮独立克隆并清理临时测试库。

- 首轮 19 文件 API、OAuth、样式、probe、浏览器 fence 窄测：335 项通过，退出 0。
  原始日志：`/tmp/tts-erp-baseline-repair-api.log`。
- 首轮 47 项浏览器及 schema 窄测：45 通过、2 失败，均为店铺冷却到期自动
  读取；最小文件复验双前缀仍失败。原始日志分别为
  `/tmp/tts-erp-baseline-repair-browser.log` 和
  `/tmp/tts-erp-baseline-repair-errors-rerun.log`。
- 修复前新组件断言实际失败：31 项中 29 通过、2 失败，均观察到到期读取
  `2 != 1`，日志 `/tmp/tts-erp-baseline-repair-cooldown-red.log`。
- 冷却修复后组件文件 31 项通过；同一隔离选择中的 adapter 和双前缀浏览器
  共 3 项通过、零跳过，退出 0，日志
  `/tmp/tts-erp-baseline-repair-cooldown-green.log`。
- 修改的 Python 文件 Ruff 检查通过。业务授权安排、OAuth、浏览器 fence、
  字体/图表及冷却修复均经过独立只读审查，无阻断问题。
- 已同步 `origin/master 1bd12fb1`。首次完整基线运行时，测试机另有独立整合
  套件并行，观察到内存 6.9/7.2 GiB、swap 近满；主动向本任务 pytest 发送
  SIGINT 以释放资源。wrapper 清理了临时库，driver 退出 2（没有完整 summary）。
  JUnit 记录部分执行 1498 项、17 个失败，不能据此声称完成完整套件或将这些
  失败直接归为稳定缺陷/偶发失败。原始日志
  `/tmp/tts-erp-baseline-repair-fast.log`，随后只按该 JUnit 精确选择失败 ID 复验。
- 对上述 17 个原失败 ID 的一次精确复验：10 项浏览器超时选择转绿，7 项稳定
  失败（5 项 `test_frontend_remaining_manual_identity.py` 授权安排缺失、
  2 项店铺筛选收宽回归），退出 1、49.368 秒，日志
  `/tmp/tts-erp-baseline-repair-failures-rerun.log`。没有把未复验的失败称为偶发。
- 补齐上述授权安排并恢复共享筛选布局后，104 项相关成本身份、店铺及全页面
  shell 浏览器回归全部通过、零跳过，退出 0，190.282 秒；包含双前缀
  375/1440/2560、原宽度上限、六个可见控件的高度及边界检查，日志
  `/tmp/tts-erp-baseline-repair-final-narrow.log`。
- 上述 104 项在已同步 `origin/master 7f4cb031` 的候选上通过。已查看
  375/1440/2560 的当前编译筛选布局截图，控件完整可见；这些 shell 截图
  展示列表错误态，带数据店铺表格的长 ID/滚动/取消覆盖来自对应原生测试。
  确认测试机没有其他 pytest 进程后启动单独完整基线。
- 单独完整 fast：3371 项中 3355 passed、15 skipped（含既有 xfail）、1 failed，
  1402.358 秒，driver 退出 1。唯一失败是组件 adapter 的 npm 子进程 180 秒
  超时，stderr 记录 ad-daily Vitest fork worker 退出超时；没有修改测试时限
  或基线来吸收它。原始日志 `/tmp/tts-erp-baseline-repair-fast-serial.log`。
- 仅复验该 adapter：`bash scripts/test_isolated.sh fast tests/frontend/test_components.py
  --tb=short`，退出 0，完整内部组件选择返回成功。按测试指南分类为可能的偶发
  worker 退出问题，不能将上一轮完整 suite 描述为全绿。日志
  `/tmp/tts-erp-baseline-repair-components-rerun.log`。
- 交付前必须在整合锁内验证实际拟合并提交；其结果和 master push 回执由任务
  最终交付记录提供。本文件保存候选修复及失败分类证据，不替代该整合验证。

未修改生产 schema、迁移、安全 guard、middleware 顺序或运行中服务。
