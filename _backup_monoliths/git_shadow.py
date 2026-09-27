"""
scripts/git_shadow.py
=====================
Backward-compatible facade for the modularized git micro-versioning layer.
All symbols are re-exported from scripts/git/ sub-modules.
"""
from __future__ import annotations

try:
    from .git import (
        ShadowGit,
        GitCredentialManager,
        push_to_remote,
        init_primary_repo,
        ensure_hardened_gitignore,
        scrub_tokens,
        discover_native_git_remote,
        is_salesforce_or_transient_file,
        HARDENED_GITIGNORE_TEMPLATE,
        IGNORED_PATH_PARTS,
        _NO_WINDOW_FLAG,
    )
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.git import (
            ShadowGit,
            GitCredentialManager,
            push_to_remote,
            init_primary_repo,
            ensure_hardened_gitignore,
            scrub_tokens,
            discover_native_git_remote,
            is_salesforce_or_transient_file,
            HARDENED_GITIGNORE_TEMPLATE,
            IGNORED_PATH_PARTS,
            _NO_WINDOW_FLAG,
        )
    except (ImportError, ModuleNotFoundError):
        from scripts.git import (
            ShadowGit,
            GitCredentialManager,
            push_to_remote,
            init_primary_repo,
            ensure_hardened_gitignore,
            scrub_tokens,
            discover_native_git_remote,
            is_salesforce_or_transient_file,
            HARDENED_GITIGNORE_TEMPLATE,
            IGNORED_PATH_PARTS,
            _NO_WINDOW_FLAG,
        )

_ensure_shadow_repo = ShadowGit._ensure_shadow_repo

__all__ = [
    "ShadowGit",
    "GitCredentialManager",
    "push_to_remote",
    "init_primary_repo",
    "ensure_hardened_gitignore",
    "scrub_tokens",
    "discover_native_git_remote",
    "is_salesforce_or_transient_file",
    "HARDENED_GITIGNORE_TEMPLATE",
    "IGNORED_PATH_PARTS",
    "_NO_WINDOW_FLAG",
]
