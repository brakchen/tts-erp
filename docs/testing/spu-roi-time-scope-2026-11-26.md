# SPU 时间分区展示验证（2026-11-26）

比较基线：`caae9dc3ffe95d64cf036fbc425520ea12bf3914`。

用户确认历史预测依据位于利润框底部，不能提到页面最前面；删除重复的店铺/SPU 范围说明。共用 React 组件同步覆盖 SPU ROI 和重点关注页，保留服务端计算及查询参数。

## 已完成

- `npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 用当前编译产物和合成只读数据在 `127.0.0.1` 人工检查：375、1440、2560 宽度无全页横向溢出；销售、成本、利润顺序保留，历史依据位于利润框底部。
- 人工操作历史样本 30→90 天：样本日期和采用样本链接更新，经营起止日保持不变。重点关注页复用同一分区，历史依据没有店铺/SPU 范围说明行。
- 更新现有组件和浏览器回归用例，覆盖常显样本、控件所属区域、日期独立性和服务端计算时间的店铺当地日期展示。

## 隔离回归补验（2026-11-27）

最初 Windows checkout 缺少测试配置、Linux Python 和 PostgreSQL 容器，未绕过隔离入口。补验在可达的 Linux 开发 VM 上使用独立 checkout，复用准备好的 Python 环境，并从现有连接 authority 派生测试形基线；每次仍由标准 runner 克隆及清理独占临时库。前端使用任务私有 Node 22.23.2，其官方 SHA256 已核验。

首次基线选择执行了 3436 个用例，完整组件套件在 VM 上超出 360 秒上限。根据仓库约定只重跑失败的最小选择，没有重复整套分支回归：

- 修正仍断言独立筛选面板的旧源码契约；该断言单独通过。
- 快速开关回归原先把 Python/CDP 往返耗时计入 300ms。将真实三次点击放在一次浏览器执行中，以 DOM 状态等待每次结果，保留 300ms 断言；单独通过。
- SPU 组件测试先等待即时响应的异步渲染；101 个真实标签的分页用例增加单例运行预算。SPU 组件 25 个用例通过。
- 全量组件中出现超时的 3 个未修改媒体标签用例单独通过。
- 独立预测样本浏览器回归通过；所有相关 runner 均成功清理各自临时库。

完整组件套件的 360 秒超时不能表述为整套已通过。最终合并提交仍需以其第一父提交为基线执行合并门禁，并按相同规则对失败作定点确认。

命令：

```bash
bash scripts/envsetup/prepare-worktree.sh
bash scripts/test_isolated.sh browser tests/browser/test_spu_projection.py
bash scripts/test_baseline.sh --scope auto --base "$target_sha" --scenario page-assets
```

`target_sha` 必须使用届时已同步的 master 完整 SHA；候选须干净且已提交。
