"""
scripts/storage package
=======================
Modular storage management for the SPD Analysis Engine.
"""
from .utils import _slug, _utcnow_iso, _remove_tree_force, purge_test_artifacts, resolve_project_identity
from .db import SessionDB
from .rotator import StorageRotator, rotate_project_to_history
from .meta import read_project_meta, update_project_meta, synthesize_project_meta, resolve_project_context
from .history import consolidate_project_history
from .rollback import rollback_burst, execute_non_destructive_rollback, undo_rollback
from .gitignore_manager import read_project_gitignore, write_project_gitignore
from .schema_migration import (
    apply_schema_migrations,
    get_user_version,
    set_user_version,
    CURRENT_SCHEMA_VERSION,
    MigrationReport,
)
from .project_storage import (
    get_durable_root,
    get_project_dir,
    ensure_project_layout,
    resolve_project_db,
    resolve_project_git,
    read_project_config,
    write_project_config,
    list_all_project_slugs,
)
from .migrator import (
    ProjectMigrator,
    MigrationResult,
    MIGRATION_MARKER_FILE,
)

from .writer import ProjectWriter
from .writer_ops import WriteOperation, WriterMetrics
from .ignore_policy import IgnorePolicy
from .manifest import FileManifest, FileManifestEntry, file_manifest
from .fingerprint import FileFingerprint, ChangeResult, compute_fingerprint, compare_fingerprints
from .compatibility_reader import (
    parse_diff_stats,
    get_table_columns,
    resolve_event_row,
    read_events_summary,
    read_event_detail,
    read_event_file_diff,
)

__all__ = [
    "SessionDB",
    "StorageRotator",
    "rotate_project_to_history",
    "_slug",
    "_utcnow_iso",
    "resolve_project_identity",
    "read_project_meta",
    "update_project_meta",
    "synthesize_project_meta",
    "resolve_project_context",
    "consolidate_project_history",
    "rollback_burst",
    "execute_non_destructive_rollback",
    "undo_rollback",
    "purge_test_artifacts",
    "read_project_gitignore",
    "write_project_gitignore",
    "apply_schema_migrations",
    "get_user_version",
    "set_user_version",
    "CURRENT_SCHEMA_VERSION",
    "MigrationReport",
    "get_durable_root",
    "get_project_dir",
    "ensure_project_layout",
    "resolve_project_db",
    "resolve_project_git",
    "read_project_config",
    "write_project_config",
    "list_all_project_slugs",
    "ProjectMigrator",
    "MigrationResult",
    "MIGRATION_MARKER_FILE",
    "ProjectWriter",
    "WriteOperation",
    "WriterMetrics",
    "IgnorePolicy",
    "FileManifest",
    "FileManifestEntry",
    "file_manifest",
    "FileFingerprint",
    "ChangeResult",
    "compute_fingerprint",
    "compare_fingerprints",
    "parse_diff_stats",
    "get_table_columns",
    "resolve_event_row",
    "read_events_summary",
    "read_event_detail",
    "read_event_file_diff",
]
