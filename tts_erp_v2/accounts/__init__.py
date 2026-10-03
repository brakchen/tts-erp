"""用户账号与页面权限体系（设计：docs/design/user-account-authz-design.md）。

- ``models``    security.* 六表 ORM
- ``pages``     页面权限点注册表（侧边栏/种子/角色表单同源）
- ``passwords`` argon2id 哈希 + 密码策略
- ``sessions``  服务端会话（token 哈希存储、吊销）
- ``service``   登录/用户/角色管理 + 授权装载
- ``cli``       运维 CLI（python -m tts_erp_v2.accounts.cli）
"""

from __future__ import annotations
