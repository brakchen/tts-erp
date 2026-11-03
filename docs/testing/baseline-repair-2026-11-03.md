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

## 测试子进程生命周期的后续修复

`11b52586` 已提交并推送；拟合并 `9ec49652` 的完整 fast 再次仅出现组件
adapter 的 180 秒超时（3371 项、3355 passed、15 skipped、1 failed，
1637.264 秒）。这推翻了用单独复验转绿就结束该问题的判断；master 尚未推送，
整合锁已释放，继续修复。原始日志 `/tmp/tts-erp-baseline-integration-fast.log`。

已确认两个 npm dev harness 只停止 npm 父进程，adapter 超时也只杀 npm；
读取 PID/PPID、启动标识、cwd 和入口路径后发现 **7 个**来自本任务两棵测试树的
孤儿 Vite。清理仅限这七个已核验 PID，发信号前再次校验启动身份，未按进程名
批量停止其它任务。是否足以解释所有超时须由后续验证确认，不能仅凭资源相关性
宣称单一根因。

三个 npm 调用改为共享的 `tests.support.owned_process` 启动/收尾 owner：
自己创建独立 Linux session，不接受外部 PID/PGID；退出时对本组 TERM，
有界宽限后仍存在则 KILL，回收直属进程并关闭自产管道。leader 已退出、后代
忽略 TERM 或关闭 stdio 都不能绕过清理。原 180 秒上限、fork 隔离、断言和
选择范围保持。实现复用标准库
[Popen](https://docs.python.org/3.14/library/subprocess.html#subprocess.Popen) 与
[killpg](https://docs.python.org/3.14/library/os.html#os.killpg)，无新依赖。

旧 API-child helper 继续服务既有 uvicorn/nonce 生命周期，本次没有把 npm
接到其依赖 leader 状态的清理路径，也没有扩大数据库/HTTP 绑定改动域。
新增真实父孙进程回归及独立 session sentinel，并在两个 Vite 测试退出后检查
本次临时端口拒绝连接；HMR、StrictMode 和图表像素断言保留。

生命周期专项的四项真实进程回归及两个 Vite 测试均通过；同轮组件 adapter
仍失败，不能把资源清理等同于已解决全部组件问题。后续确认并拆除了本任务
测试树指向其它任务 `node_modules` 的链接，按 lockfile 独立安装，并显式使用
满足 engine 的 Node 22.22.2。未改动被链接的其它任务依赖目录。

Vitest 5.0.5 的默认并发在该 8 CPU、7.2 GiB 虚拟机上启动七个 jsdom fork。
采用官方 [maxWorkers](https://vitest.dev/config/maxworkers) 将上限设为 2，
保留文件隔离、原有 180 秒 adapter 上限及单项测试时限。完整组件窄测在
124.22 秒内结束：705 项中 704 通过、1 失败；本任务 frontend 进程 RSS
采样峰值约 1.38 GiB，宿主可用内存最低约 2.88 GiB。日志
`/tmp/tts-erp-baseline-bounded-components.log`，完整内部输出保存在 pytest 的
`TEST_components.log`，避免尾部截断遮蔽首个失败。

唯一剩余失败为广告日报 HTTP 500 用例：固定真实等待 1100 毫秒后立即读取
React 状态，观察到按钮仍 disabled。改用文件中已有的 fake timer + `act`
模式，明确检查 999 毫秒时仍禁用、1001 毫秒时启用，两个阶段均无自动读取，
只有显式刷新才增加 options/list 两次请求。四个 HTTP 状态继续保留安全错误
显示及不暴露原响应断言。该文件通过隔离 adapter 复验退出 0，日志
`/tmp/tts-erp-baseline-ad-clock.log`。没有增加重试、放宽断言或延长测试时限。

未修改生产 schema、迁移、安全 guard、middleware 顺序或运行中服务。
