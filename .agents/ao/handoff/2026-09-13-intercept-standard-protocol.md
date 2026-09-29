# Handoff: intercept standard wire protocol

## Goal

修复 `tts-request-intercept` 与 `tts-erp` 的拦截同步契约，统一使用单一 camelCase wire protocol，不在后端保留 snake_case 兼容输入。

## Changes

- `tts_erp_v2/api/v2/intercept.py`
  - `session`、`scope`、请求记录字段全部用显式 camelCase alias 映射到内部 snake_case 属性。
  - 同步模型使用 `extra="forbid"`，snake_case 或未知 wire 字段直接返回 422。
  - request/response body 接受任意 JSON 类型，空对象/空数组不再丢失。
  - 请求、会话统计、同步游标合并为一个提交事务；会话计数只累计实际插入的 request_id。
  - cursor 返回标准字段 `totalSynced`，统计接口补 `days` 和 `daily`。
- `tests/api/test_intercept_sync.py`
  - 测试报文改为与插件完全一致的 camelCase。
  - 增加 scalar body、空 JSON、重复会话计数和拒绝 snake_case 的回归覆盖。
- `tts_erp_v2/static/js/intercept-stats.js`
  - 统计分布兼容当前数组响应，渲染每日趋势。

## Verification

- `python -m compileall -q tts_erp_v2/api/v2/intercept.py tests/api/test_intercept_sync.py`: passed.
- Backend integration suite not run in this Windows environment: `scripts/test.sh` has CRLF `pipefail` parsing failure and the local Python lacks `sqlalchemy`/test DB.
- Extension `npm test`: 2 files, 4 tests passed.
- Extension `npx tsc --noEmit`: passed.
- Extension ZIP: `tts-request-intercept-0.1.4-chrome.zip`, manifest version `0.1.4`.

## Next agent

1. In a backend environment with dependencies and the test database, run the focused intercept suite and full `bash scripts/test.sh fast`.
2. Merge `fix/intercept-contract` into master only after code-lane regression comparison passes.
3. Push master and deploy/restart the public TTS-ERP service.
4. Install the 0.1.4 extension and verify a real sync request returns 200 and inserts a row.
