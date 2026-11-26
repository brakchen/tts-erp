# SPU 时间分区展示验证（2026-11-26）

比较基线：`caae9dc3ffe95d64cf036fbc425520ea12bf3914`。

用户确认历史预测依据位于利润框底部，不能提到页面最前面；删除重复的店铺/SPU 范围说明。共用 React 组件同步覆盖 SPU ROI 和重点关注页，保留服务端计算及查询参数。

## 已完成

- `npm run typecheck --prefix frontend`、`npm run build --prefix frontend` 通过。
- 用当前编译产物和合成只读数据在 `127.0.0.1` 人工检查：375、1440、2560 宽度无全页横向溢出；销售、成本、利润顺序保留，历史依据位于利润框底部。
- 人工操作历史样本 30→90 天：样本日期和采用样本链接更新，经营起止日保持不变。重点关注页复用同一分区，历史依据没有店铺/SPU 范围说明行。
- 更新现有组件和浏览器回归用例，覆盖常显样本、控件所属区域、日期独立性和服务端计算时间的店铺当地日期展示。

## 未完成的隔离回归

当前 Windows checkout 缺少 `.venv/bin/python`、`.env.test`、可用的 Docker PostgreSQL 容器及测试模板。`bash scripts/envsetup/install-test-deps.sh --check` 已输出上述缺项（其退出码为 0，不能据此视为依赖通过）。默认 WSL Bash 还会拒绝 checkout 中的 CRLF shell 脚本。检查依赖时临时规范化的脚本已恢复，不纳入本次提交。

未绕过 `scripts/test_isolated.sh` 直接运行 pytest/Vitest，也未使用共享数据库。当前证据仅为类型、构建和合成数据视觉检查，不代表真实 API/数据库集成回归通过，不满足合并条件。

在准备好的 Linux/WSL 测试环境，同步最新 master 后执行：

```bash
bash scripts/envsetup/prepare-worktree.sh
bash scripts/test_isolated.sh browser tests/browser/test_spu_projection.py
bash scripts/test_baseline.sh --scope auto --base "$target_sha" --scenario page-assets
```

`target_sha` 必须使用届时已同步的 master 完整 SHA；候选须干净且已提交。
