"""Pure deployment-path canonicalization."""

from __future__ import annotations

from tts_erp_v2.access._types import CanonicalPath, DeploymentPathInput


def canonicalize_path(value: DeploymentPathInput) -> CanonicalPath:
    """Return downstream and route-relative paths for either proxy shape."""

    path = value.path
    root_path = value.root_path
    downstream_path = path

    if root_path and path != root_path and not path.startswith(root_path + "/"):
        downstream_path = root_path + path

    route_path = downstream_path
    if root_path and downstream_path.startswith(root_path):
        if len(downstream_path) == len(root_path):
            route_path = "/"
        elif downstream_path[len(root_path)] == "/":
            route_path = downstream_path[len(root_path) :]

    downstream_raw_path = (
        downstream_path.encode("latin-1") if value.raw_path is not None else None
    )
    return CanonicalPath(
        downstream_path=downstream_path,
        downstream_raw_path=downstream_raw_path,
        route_path=route_path,
        root_path=root_path,
    )
