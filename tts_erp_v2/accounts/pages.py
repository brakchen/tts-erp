"""页面权限点注册表（设计：tech-doc/user-account-authz-design.md §7.3）。

权限点与侧边栏页面一一对应（``page:<page_id>``），是权限体系的唯一页面清单
来源：侧边栏渲染、alembic 种子、角色编辑表单共用本清单。
新增页面时在此登记 + migration/CLI 补 permissions 行 + 给角色授权。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PageDef:
    page_id: str
    icon: str
    label: str
    group: str

    @property
    def permission_code(self) -> str:
        return f"page:{self.page_id}"


# 顺序 = 侧边栏渲染顺序（分组内自上而下）。
PAGES: tuple[PageDef, ...] = (
    PageDef("dashboard", "台", "控制台", "总览"),
    PageDef("focused-spus", "关", "重点关注 SPU", "经营分析"),
    PageDef("spu-roi", "益", "SPU ROI", "经营分析"),
    PageDef("ad-daily", "广", "广告日明细", "经营分析"),
    PageDef("manual-costs", "采", "采购工作台", "基础设置"),
    PageDef("shops", "店", "店铺注册", "基础设置"),
    PageDef("enum-map", "映", "枚举映射", "基础设置"),
    PageDef("runtime-configs", "运", "运行配置", "基础设置"),
    PageDef("sync-jobs", "时", "定时任务", "基础设置"),
    PageDef("users", "员", "用户管理", "基础设置"),
    PageDef("intercept-configs", "配", "拦截配置", "数据工具"),
    PageDef("intercept-requests", "录", "拦截记录", "数据工具"),
    PageDef("intercept-stats", "计", "拦截统计", "数据工具"),
)

PAGE_BY_ID: dict[str, PageDef] = {p.page_id: p for p in PAGES}

ALL_PERMISSION_CODES: tuple[str, ...] = tuple(p.permission_code for p in PAGES)


def permission_catalog() -> list[dict]:
    """全部页面权限点目录（角色编辑表单的勾选清单，同源于侧边栏注册表）."""
    return [
        {"code": p.permission_code, "label": p.label, "group": p.group}
        for p in PAGES
    ]

# 预置角色（§2.1）：code → (api_tier, 页面 id 集合)。`users` 页面仅 admin。
BUILTIN_ROLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "admin": ("admin", tuple(p.page_id for p in PAGES)),
    "operator": (
        "readwrite",
        tuple(p.page_id for p in PAGES if p.page_id != "users"),
    ),
    "viewer": (
        "readonly",
        ("dashboard", "focused-spus", "spu-roi", "ad-daily", "intercept-stats"),
    ),
}

BUILTIN_ROLE_NAMES: dict[str, str] = {
    "admin": "系统管理员",
    "operator": "运营",
    "viewer": "只读分析",
}

# 页面 id → 该页面写操作所需的最低 api_tier（§7.2 提示用；只读页面无此约束）。
PAGE_MIN_WRITE_TIER: dict[str, str] = {
    "manual-costs": "readwrite",
    "shops": "readwrite",
    "enum-map": "readwrite",
    "runtime-configs": "readwrite",
    "sync-jobs": "readwrite",
    "users": "admin",
    "intercept-configs": "readwrite",
    "intercept-requests": "readwrite",
}


def required_page_permission(route_path: str) -> str | None:
    """路由 → 页面权限点（无页面语义的路由返回 None，走 api_tier 矩阵）.

    - ``/v2/pages/<id>``   → ``page:<id>``
    - ``/v2/users*`` ``/v2/roles*`` → ``page:users``（用户管理页的配套 API）
    - 其余（数据 API）→ None：页面内全部操作不设权限点（设计 §7.1），
      由会话的 api_tier 走既有路由角色矩阵兜底。
    """
    path = route_path.split("?", 1)[0]
    if path.startswith("/v2/pages/"):
        page_id = path[len("/v2/pages/") :].strip("/")
        if "/" in page_id or not page_id:
            return None
        return f"page:{page_id}"
    if path.startswith(("/v2/users", "/v2/roles")):
        return "page:users"
    return None
