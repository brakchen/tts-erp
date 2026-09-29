# TikTok HMAC 签名规范

> 本文档包含 TikTok Shop API 的 HMAC 签名规范。
> 通用约束和边界规则见 `AGENTS.md`。

## 1. 签名格式（最常出错）

```text
canonical POST: {app_secret}{path}{app_key}{value}{shop_cipher}{value}{timestamp}{value}{body}{app_secret}
canonical GET : {app_secret}{path}{app_key}{value}{shop_cipher}{value}{timestamp}{value}{app_secret}
```

## 2. 关键规则

- **keys 按字母序**：app_key < shop_cipher < timestamp
- **shop_cipher 位置**：永远在 **query**（GET/POST 都是）
- **body 格式**：`json.dumps(..., ensure_ascii=False)` 的**原始字符串**，在 KV 串之后、结尾 secret 之前
- **⚠ 千万不要 URL-encode body**（实测全部 106001 invalid sign）

## 3. 实现位置

- **签名实现**：`tts_erp_v2/proxy/tts_shop/signing.py`
- **调试命令**：`TTS_DEBUG_SIGN=1` 打 canonical

## 4. 常见错误

| 错误码 | 原因 | 修复 |
| --- | --- | --- |
| `106001 invalid sign` | 签名格式错（最常见） | `TTS_DEBUG_SIGN=1` 看 canonical，对比本文档 |
| `105005 Access denied` | app 没勾 scope | Partner Center 改 app scope + 重新授权 |
| `36009004 PageSize is required` | body 字段名/格式错 | 查 TikTok API 文档 Request Body 章节 |

## 5. 注意事项

- **不要假设 TikTok `code: 0` 是唯一 success**：也有 `105005` scope 缺失 / `36009004` 字段缺失等
- **打上游 TikTok 的唯一路径**：sync-worker jobs（经 `tts_erp_v2/proxy/tts_shop`，内部处理 HMAC 签名 /
  `x-tts-access-token` / shop_cipher 位置 / 翻页 / 过期 token 续期）。**不要自己拼上游请求**。
