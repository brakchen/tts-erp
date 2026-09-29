"""Public interface for deployment-path and access decisions."""

from tts_erp_v2.access._access import evaluate_access as evaluate_access
from tts_erp_v2.access._credentials import (
    authenticate_hash as authenticate_hash,
    authenticate_key as authenticate_key,
    clear_credential_cache as clear_credential_cache,
    hash_key as hash_key,
)
from tts_erp_v2.access._deployment import canonicalize_path as canonicalize_path
from tts_erp_v2.access._policy import required_role as required_role
from tts_erp_v2.access._types import (
    AccessDecision,
    AccessEffect,
    AccessGrant,
    AccessRequest,
    AuthMode,
    CanonicalPath,
    Credential,
    DeploymentPathInput,
    Role,
)

__all__ = [
    "AccessDecision",
    "AccessEffect",
    "AccessGrant",
    "AccessRequest",
    "AuthMode",
    "CanonicalPath",
    "Credential",
    "DeploymentPathInput",
    "Role",
    "authenticate_hash",
    "authenticate_key",
    "canonicalize_path",
    "clear_credential_cache",
    "evaluate_access",
    "hash_key",
    "required_role",
]
