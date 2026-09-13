# Handoff: intercept sync repair

## Caller goal

修复 `tts-request-intercept` 与 `tts-erp` 的拦截请求同步问题；在后端推送前留下可验证的交接说明，供下一位 agent 继续部署和验证。

## Completed artifacts

- `tts_erp_v2/api/v2/intercept.py`
  - 单条同步写入使用 nested transaction，单条异常不再污染整个批次事务。
  - `accepted` 表示处理成功的输入项，`inserted` 表示实际新增行；重复 `request_id` 不再推进 `total_synced`。
  - 游标 scope 同时兼容 `sellerId` 和 `seller_id`。
  - 统计响应保留原字段并增加前端兼容别名。
- `tests/api/test_intercept_sync.py`
  - 覆盖重复同步不增加插入游标。
  - 覆盖 camelCase `sellerId` 游标键。
- 后端当前分支：`master`；远程：`origin` (`git@github.com:brakchen/tts-erp.git`)。
- 本地提交：`c558ec5 fix: 修复拦截同步统计与事务处理`，已合并为 `c393a07 merge: 修复拦截同步问题`。

## Evidence and gaps

- `python -m compileall -q tts_erp_v2/api/v2/intercept.py tests/api/test_intercept_sync.py`：通过。
- `scripts/test.sh fast tests/api/test_intercept_sync.py -q`：当前 Windows 工作区因脚本 CRLF 导致 `pipefail\r` 解析失败。
- 直接执行 pytest：当前环境缺少 `sqlalchemy`，无法导入后端测试夹具；因此后端回归测试尚未获得运行结果。
- 代码尚未部署到公网服务，也没有重启服务；`http://daqiang.nat100.top/tts` 的线上修复状态未知。

## Next agent continuation

下一位 agent 应在具备后端运行环境后：

1. 安装/启用后端依赖和测试数据库环境，运行 `tests/api/test_intercept_sync.py`，再按 `AGENTS.md` 执行完整测试与检查。
2. 将 `master` 的 `c393a07` 部署到 TTS-ERP 服务器并重启服务。
3. 使用有效 Bearer API Key 验证 `POST /v2/intercept/sync`：重复请求的 `inserted` 和 `total_synced` 不应增加，`sellerId` scope 应生成对应游标。
4. 重新加载插件 `tts-request-intercept-0.1.1`，在真实 TikTok 页面确认同步请求、会话和请求记录均能落库。

## Repository identity

- Source repository: `git@github.com:brakchen/tts-erp.git`
- Source branch: `master`
- Parent/resume task IDs: unknown in this handoff context.
