# 妙手开放平台

> 本文档包含 tts-erp 项目中妙手开放平台的详细信息。
> 通用约束和边界规则见 `AGENTS.md`。

## 1. 概述

**平台名称**：妙手开放平台（apifox 标题）

**底层 endpoint**：`openapi.wanshifu.com`

**SDK 包位置**：`miaoshou/`（`MiaoshouClient` / `MiaoshouErpClient` + `miaoshou_signing.py`）

**规模**：36 出站 endpoint + 18 回调 payload

**运行方式**：进程内被 jobs 用，**不**单独起服务

## 2. 接入方式

**人类开发者操作**：申请 licenseId + companySecret 写 .env `MIAOSHOU_*`

**测试账号**：test-user.wanshifu.com

**agent 不代办**：接入申请是人类开发者操作，agent 不代办

## 3. 调度状态

**查看方式**：`sync_worker/scheduler.py` 顶部 `NOTE`

**说明**：谁注册了 / 谁故意不注册 → 以那里为准，勿重复维护

## 4. 签名规范

**参考文档**：apifox doc-824327

**签名算法**：

```python
busData = base64(json.dumps(params, ensure_ascii=False))
sign = MD5(busData + companySecret).upper()
```

**envelope 格式**：

- licenseId
- companySecret
- sign
- busData
- timestamp（毫秒）

**锁定向量**：`tests/miaoshou/test_signing.py::test_build_sign_doc_824327_vector`

## 5. 测试

```bash
# 妙手 SDK 测试（91 个 test / 15 文件）
.venv/bin/pytest tests/miaoshou/ -q

# 妙手 jobs 测试
.venv/bin/pytest tests/jobs_miaoshou/ -q
```

## 6. 注意事项

- **Miaoshou 已无任何 HTTP 面**：不要等它回来
- **出站代理和回调端点未挂 v2**：实测 404
- **签名调试**：`MIAOSHOU_DEBUG_SIGN=1` 在 stderr 打 canonical
