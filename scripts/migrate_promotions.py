"""Safe additive migration runner for the LIXAFA promotion management schema."""

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import PROMOTION_MIGRATION_ID, get_database_url, get_engine, init_db


def _sqlite_path(database_url: str) -> Optional[Path]:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    path = Path(url.database)
    return path if path.is_absolute() else (ROOT / path).resolve()


def backup_sqlite(database_url: str, destination: Optional[Path] = None) -> Optional[Path]:
    source = _sqlite_path(database_url)
    if source is None or not source.exists():
        return None
    backup_dir = ROOT / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = destination or backup_dir / f"lixafa-before-promotions-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.db"
    target = target.resolve()
    with sqlite3.connect(str(source)) as source_db, sqlite3.connect(str(target)) as target_db:
        source_db.backup(target_db)
    return target


async def preflight() -> Dict[str, Any]:
    engine = get_engine()
    async with engine.connect() as conn:
        sqlite = str(engine.url).startswith("sqlite")
        if sqlite:
            tables_result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            tables = {row[0] for row in tables_result.fetchall()}
        else:
            tables_result = await conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
            )
            tables = {row[0] for row in tables_result.fetchall()}
        if "promos" not in tables:
            return {"database": engine.url.drivername, "promo_count": 0, "missing_promos_table": True}
        promo_count = int((await conn.execute(text("SELECT COUNT(*) FROM promos"))).scalar_one() or 0)
        duplicate_vouchers = [
            {"normalized_code": row[0], "count": int(row[1])}
            for row in (
                await conn.execute(
                    text(
                        """
                        SELECT UPPER(TRIM(code)), COUNT(*)
                        FROM promos
                        WHERE code IS NOT NULL AND TRIM(code)<>''
                        GROUP BY UPPER(TRIM(code))
                        HAVING COUNT(*) > 1
                        """
                    )
                )
            ).fetchall()
        ]
        orphan_codes: list[Dict[str, Any]] = []
        if "topup" in tables:
            orphan_codes = [
                {"code": row[0], "order_count": int(row[1])}
                for row in (
                    await conn.execute(
                        text(
                            """
                            SELECT t.promo_code, COUNT(*)
                            FROM topup t
                            WHERE COALESCE(TRIM(t.promo_code), '')<>''
                              AND NOT EXISTS (
                                  SELECT 1 FROM promos p
                                  WHERE UPPER(TRIM(p.code))=UPPER(TRIM(t.promo_code))
                              )
                            GROUP BY t.promo_code
                            """
                        )
                    )
                ).fetchall()
            ]
        return {
            "database": engine.url.drivername,
            "promo_count": promo_count,
            "duplicate_vouchers": duplicate_vouchers,
            "orphan_order_codes": orphan_codes,
        }


async def upgrade(*, skip_backup: bool = False, postgres_backup_confirmed: bool = False) -> Dict[str, Any]:
    database_url = get_database_url()
    sqlite_path = _sqlite_path(database_url)
    backup_path: Optional[Path] = None
    if sqlite_path is not None and not skip_backup:
        backup_path = backup_sqlite(database_url)
    elif sqlite_path is None and not skip_backup and not postgres_backup_confirmed:
        raise RuntimeError("PostgreSQL migration requires a verified pg_dump; pass --postgres-backup-confirmed")

    before = await preflight()
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        required_tables = {
            "promotion_target_catalog",
            "promotion_targets",
            "promotion_customer_targets",
            "promotion_redemptions",
            "schema_migrations",
        }
        if str(engine.url).startswith("sqlite"):
            result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        else:
            result = await conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
            )
        tables = {row[0] for row in result.fetchall()}
        missing = sorted(required_tables - tables)
        if missing:
            raise RuntimeError(f"Migration verification failed; missing tables: {', '.join(missing)}")
        marker = await conn.execute(
            text("SELECT checksum, applied_at FROM schema_migrations WHERE migration_id=:id"),
            {"id": PROMOTION_MIGRATION_ID},
        )
        marker_row = marker.first()
    return {
        "success": True,
        "migration_id": PROMOTION_MIGRATION_ID,
        "backup": str(backup_path) if backup_path else None,
        "preflight": before,
        "marker": {"checksum": marker_row[0], "applied_at": str(marker_row[1])} if marker_row else None,
    }


async def rollback_non_destructive() -> Dict[str, Any]:
    """Remove v2-only uniqueness gates/marker; all data and additive columns remain intact."""

    engine = get_engine()
    async with engine.begin() as conn:
        for index_name in ("uq_promos_voucher_code_ci", "uq_promos_internal_code_ci"):
            await conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
        marker_removed = True
        marker_warning = None
        try:
            await conn.execute(
                text("DELETE FROM schema_migrations WHERE migration_id=:id"),
                {"id": PROMOTION_MIGRATION_ID},
            )
        except Exception as exc:
            # A database that never completed the upgrade has no marker to remove.
            marker_removed = False
            marker_warning = str(exc)
    return {
        "success": True,
        "migration_id": PROMOTION_MIGRATION_ID,
        "mode": "non-destructive",
        "marker_removed": marker_removed,
        "marker_warning": marker_warning,
        "message": "Schema/data v2 tetap disimpan; deploy kode lama dapat membaca kolom legacy.",
    }


async def _run(args: argparse.Namespace) -> None:
    if args.command == "preflight":
        result = await preflight()
    elif args.command == "upgrade":
        result = await upgrade(
            skip_backup=args.skip_backup,
            postgres_backup_confirmed=args.postgres_backup_confirmed,
        )
    else:
        result = await rollback_non_destructive()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    await get_engine().dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="LIXAFA promotion schema migration")
    parser.add_argument("command", choices=("preflight", "upgrade", "rollback"))
    parser.add_argument("--skip-backup", action="store_true", help="Only for disposable/test databases")
    parser.add_argument(
        "--postgres-backup-confirmed",
        action="store_true",
        help="Confirm that pg_dump was completed and verified before upgrade",
    )
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
