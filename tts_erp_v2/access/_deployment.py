"""Pure deployment-path canonicalization."""

from __future__ import annotations

from urllib.parse import quote

from tts_erp_v2.access._types import CanonicalPath, DeploymentPathInput


def canonicalize_path(value: DeploymentPathInput) -> CanonicalPath:
    """Return downstream and route-relative paths for either proxy shape."""

    path = value.path
    root_path = value.root_path
    downstream_path = path
    added_root_prefix = bool(
        root_path and path != root_path and not path.startswith(root_path + "/")
    )

    if added_root_prefix:
        downstream_path = root_path + path

    route_path = downstream_path
    if root_path and downstream_path.startswith(root_path):
        if len(downstream_path) == len(root_path):
            route_path = "/"
        elif downstream_path[len(root_path)] == "/":
            route_path = downstream_path[len(root_path) :]

    downstream_raw_path = value.raw_path
    if added_root_prefix and downstream_raw_path is not None:
        root_raw = quote(root_path, safe="/").encode("ascii")
        downstream_raw_path = root_raw + downstream_raw_path
    return CanonicalPath(
        downstream_path=downstream_path,
        downstream_raw_path=downstream_raw_path,
        route_path=route_path,
        root_path=root_path,
    )
