"""Public interface for deployment-path and access decisions."""

from tts_erp_v2.access._deployment import canonicalize_path
from tts_erp_v2.access._types import CanonicalPath, DeploymentPathInput

__all__ = [
    "CanonicalPath",
    "DeploymentPathInput",
    "canonicalize_path",
]
