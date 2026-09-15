# `security.api_keys.role` — API key 角色

> API key 鉴权的三级角色（`tech-doc/api-key-auth-design.md`）。

## 来源
- DB column: `security.api_keys.role` (Text, NOT NULL)
- 类型: **text**（项目内部定义）
- 文档锚点: `tts_erp_v2/db/models/security.py:12-14`（注释）、`tech-doc/api-key-auth-design.md`

## 取值（✅ 固化，3 级）

| 值 | 含义 | 权限 |
| --- | --- | --- |
| `readonly` | 只读 | GET 类端点 |
| `readwrite` | 读写 | GET + POST/PUT（不含 destructive） |
| `admin` | 管理员 | 全部（含 destructive 端点） |

## 引用
- 代码: `tts_erp_v2/db/models/security.py:12-14`、`tts_erp_v2/middleware/auth.py`
- 文档: `tech-doc/api-key-auth-design.md`、`tech-doc/external-api.md` 角色矩阵
