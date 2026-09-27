"""
scripts/git package
===================
Modular Git micro-versioning and credential management for the SPD Analysis Engine.
"""
from .utils import (
    ensure_hardened_gitignore,
    scrub_tokens,
    discover_native_git_remote,
    is_salesforce_or_transient_file,
    HARDENED_GITIGNORE_TEMPLATE,
    IGNORED_PATH_PARTS,
    _NO_WINDOW_FLAG,
)
from .shadow_repo import ShadowGit
from .shadow_rollback import ShadowRollbackMixin
from .shadow_push import push_to_remote_repo
from .credentials import GitCredentialManager
from .remote_sync import push_to_remote, init_primary_repo

__all__ = [
    "ShadowGit",
    "GitCredentialManager",
    "push_to_remote",
    "push_to_remote_repo",
    "init_primary_repo",
    "ensure_hardened_gitignore",
    "scrub_tokens",
    "discover_native_git_remote",
    "is_salesforce_or_transient_file",
]
