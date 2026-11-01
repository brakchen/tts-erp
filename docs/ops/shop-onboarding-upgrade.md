# 店铺接入数据库升级

本次升级目标固定为 `0072_shop_app_credentials`。它会应用当前数据库尚未执行、且通向该版本的迁移；不会启动服务、安装依赖、补录历史店铺或执行测试。

## Windows 一键执行

在项目根目录双击 `upgrade-database.cmd`。脚本优先加载 `.env.local`，不存在时加载 `.env`；选中文件中的配置优先于同名环境变量。窗口显示配置文件和数据库主机/端口/库名，成功或失败后均停留，密码和 App Secret 不输出。

请在目标环境执行；如果配置连接远程数据库，本次修改就作用于该远程数据库。服务升级期间先停止旧 API/sync worker，成功后再启动配套新版。脚本仅在本次子进程设置现有迁移守卫要求的 `ALLOW_PROD_DESTRUCTIVE=1`，不修改配置文件。

如需先确认目标，不连接数据库：

```powershell
.\.venv\Scripts\python.exe scripts\upgrade_database.py --check-only
```

指定其他配置文件：

```powershell
.\upgrade-database.cmd --env-file .env
```

脚本要求项目 `.venv` 已安装依赖。也可以在已安装项目依赖的 Linux 环境运行 `python scripts/upgrade_database.py --env-file .env`。

## 迁移内容

- `0071_shop_discovery`：在现有店铺表增加插件元数据字段。
- `0072_shop_app_credentials`：App 配置改为按店铺隔离，保留旧配置并复制对应配置给店铺；旧环境变量中的完整 App 配置会按现有绑定一次性加密导入，数据库已有配置优先。
- 迁移使用原有 `TTS_ERP_FERNET_KEY`；不要在升级时更换密钥。
- 已升级到目标版本时，再次执行不会重复应用迁移。

迁移尚未在本次开发会话执行。失败时根据窗口中的异常类型和数据库错误码排查；不要通过 `alembic stamp` 跳过失败迁移。
