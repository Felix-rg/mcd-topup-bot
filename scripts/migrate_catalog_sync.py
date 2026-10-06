"""Explicit owner-runner for the additive LIXAFA catalog schema migration.

This runner deliberately does not import application database helpers or call
``init_db()``.  It must never be attached to application startup, Railway
Pre-Deploy, or a scheduler.
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from catalog_schema import (  # noqa: E402
    CatalogMigrationError,
    DatabaseTargetError,
    MigrationVerificationError,
    cli_error_payload,
    preflight_catalog_schema,
    upgrade_catalog_schema,
    verify_catalog_schema,
)


def _optional_path(value: str) -> Path:
    return Path(value).expanduser()


def _database_url_from_args(args: argparse.Namespace) -> str:
    if args.database_url:
        return str(args.database_url)
    environment_name = str(args.database_url_env or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", environment_name):
        raise DatabaseTargetError("Database URL environment variable name is invalid")
    database_url = str(os.environ.get(environment_name) or "").strip()
    if not database_url:
        raise DatabaseTargetError("Database URL environment variable is not set")
    return database_url


async def _run(args: argparse.Namespace) -> Dict[str, Any]:
    sqlite_backup_path = args.sqlite_backup_path
    postgres_backup_manifest = args.postgres_backup_manifest
    database_url = _database_url_from_args(args)

    if args.command == "preflight":
        return await preflight_catalog_schema(
            database_url,
            sqlite_backup_path=sqlite_backup_path,
            postgres_backup_manifest=postgres_backup_manifest,
        )
    if args.command == "upgrade":
        return await upgrade_catalog_schema(
            database_url,
            target_fingerprint=args.target_fingerprint,
            sqlite_backup_path=sqlite_backup_path,
            postgres_backup_manifest=postgres_backup_manifest,
            sqlite_engine_stopped=bool(args.sqlite_engine_stopped),
        )

    result = await verify_catalog_schema(
        database_url,
        target_fingerprint=args.target_fingerprint,
    )
    if not result.get("valid"):
        raise MigrationVerificationError("Catalog schema verification did not pass")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explicit, checksum-verified LIXAFA catalog schema migration",
    )
    parser.add_argument("command", choices=("preflight", "upgrade", "verify"))
    target_input = parser.add_mutually_exclusive_group(required=True)
    target_input.add_argument(
        "--database-url",
        help="Explicit SQLite or PostgreSQL target. It is never printed by this runner.",
    )
    target_input.add_argument(
        "--database-url-env",
        help="Environment variable containing the target URL; preferred when it carries credentials.",
    )
    parser.add_argument(
        "--target-fingerprint",
        help="Required for upgrade/verify; obtain it from the read-only preflight output.",
    )
    parser.add_argument(
        "--sqlite-backup-path",
        type=_optional_path,
        help="Required for the first SQLite upgrade; created/verified or re-verified with a manifest.",
    )
    parser.add_argument(
        "--postgres-backup-manifest",
        type=_optional_path,
        help="Required for the first PostgreSQL upgrade; must describe a verified pg_dump artifact.",
    )
    parser.add_argument(
        "--sqlite-engine-stopped",
        action="store_true",
        help="Required acknowledgement that the application/engine is stopped for a SQLite upgrade.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command in {"upgrade", "verify"} and not str(args.target_fingerprint or "").strip():
        parser.error("--target-fingerprint is required for upgrade and verify")

    try:
        result = asyncio.run(_run(args))
    except CatalogMigrationError as exc:
        print(json.dumps(cli_error_payload(exc), ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(2)
    except Exception:
        # DBAPI errors can embed URLs or credentials.  Keep unexpected failures
        # safe for terminal/audit output and require owner investigation.
        print(
            json.dumps(
                {
                    "success": False,
                    "error_code": "unexpected_migration_error",
                    "message": "Catalog migration stopped; inspect local diagnostics without exposing credentials.",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        raise SystemExit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
