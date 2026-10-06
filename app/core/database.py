import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.settings import Settings, settings

logger = logging.getLogger(__name__)


def get_database_url(app_settings: Optional[Settings] = None) -> str:
    runtime_settings = app_settings or Settings()
    configured_url = str(runtime_settings.database_url or "").strip()
    if not configured_url:
        raise RuntimeError(
            "DATABASE_URL belum dikonfigurasi. Development SQLite harus memakai URL sqlite+aiosqlite eksplisit."
        )
    if runtime_settings.is_production and configured_url.lower().startswith("sqlite"):
        raise RuntimeError("Production tidak boleh menggunakan atau fallback ke SQLite.")
    return configured_url


def _masked_database_host(host: Optional[str]) -> str:
    cleaned = str(host or "").strip()
    if not cleaned:
        return "local"
    parts = cleaned.split(".")
    if len(parts) == 4 and all(part.isdigit() for part in parts):
        return f"{parts[0]}.***.***.{parts[-1]}"
    if len(parts) > 1:
        return f"{parts[0]}.***"
    if len(cleaned) <= 2:
        return "***"
    return f"{cleaned[:2]}***"


def database_runtime_summary(
    database_url: Optional[str] = None,
    *,
    app_settings: Optional[Settings] = None,
) -> dict[str, str]:
    runtime_settings = app_settings or Settings()
    parsed = make_url(database_url or get_database_url(runtime_settings))
    database_name = str(parsed.database or "")
    if parsed.drivername.startswith("sqlite"):
        database_name = Path(database_name).name or database_name or ":memory:"
    return {
        "engine": parsed.drivername,
        "host": _masked_database_host(parsed.host),
        "database": database_name,
        "environment": runtime_settings.app_env,
    }


def log_database_runtime(
    database_url: Optional[str] = None,
    *,
    app_settings: Optional[Settings] = None,
) -> dict[str, str]:
    summary = database_runtime_summary(database_url, app_settings=app_settings)
    logger.info(
        "Database runtime: engine=%s host=%s database=%s environment=%s",
        summary["engine"],
        summary["host"],
        summary["database"],
        summary["environment"],
    )
    return summary


engine: Optional[AsyncEngine] = None


def _default_products() -> list[dict[str, object]]:
    return [
        {
            "sku": "ml_10k",
            "provider": "Mobile Legends",
            "name": "Diamonds 10K",
            "cost_price": 8500,
            "price": 10000,
            "active": 1,
            "category": "Mobile Legends",
            "description": "Top up cepat untuk Mobile Legends",
            "image_url": "",
            "logo_url": "",
            "promo_title": "Best Seller",
            "promo_text": "Pengiriman instan dan harga bersahabat",
            "promo_badge": "Hot",
            "promo_url": "",
        },
        {
            "sku": "ff_12k",
            "provider": "Free Fire",
            "name": "Diamonds 12K",
            "cost_price": 10000,
            "price": 12000,
            "active": 1,
            "category": "Free Fire",
            "description": "Top up Free Fire dengan proses cepat",
            "image_url": "",
            "logo_url": "",
            "promo_title": "Promo Harian",
            "promo_text": "Harga transparan dan aman",
            "promo_badge": "New",
            "promo_url": "",
        },
        {
            "sku": "pubg_20k",
            "provider": "PUBG Mobile",
            "name": "UC 20K",
            "cost_price": 17000,
            "price": 20000,
            "active": 1,
            "category": "PUBG Mobile",
            "description": "Top up UC PUBG Mobile",
            "image_url": "",
            "logo_url": "",
            "promo_title": "Popular",
            "promo_text": "Pilihan favorit gamer",
            "promo_badge": "Top",
            "promo_url": "",
        },
    ]


async def _ensure_topup_retry_columns(db_engine: AsyncEngine) -> None:
    if str(db_engine.url).startswith("sqlite"):
        async with db_engine.begin() as conn:
            result = await conn.execute(text("PRAGMA table_info(topup)"))
            existing_columns = {row[1] for row in result.fetchall()}

            if "provider_retry_count" not in existing_columns:
                await conn.execute(text("ALTER TABLE topup ADD COLUMN provider_retry_count INTEGER DEFAULT 0"))
            if "provider_last_error" not in existing_columns:
                await conn.execute(text("ALTER TABLE topup ADD COLUMN provider_last_error TEXT"))
        return

    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(
                """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'topup'
            """
            )
        )
        existing_columns = {row[0] for row in result.fetchall()}

        if "provider_retry_count" not in existing_columns:
            await conn.execute(text("ALTER TABLE topup ADD COLUMN provider_retry_count INTEGER DEFAULT 0"))
        if "provider_last_error" not in existing_columns:
            await conn.execute(text("ALTER TABLE topup ADD COLUMN provider_last_error TEXT"))


async def _ensure_topup_business_columns(db_engine: AsyncEngine) -> None:
    extra_columns = {
        "order_type": "TEXT DEFAULT 'PREPAID'",
        "price": "NUMERIC",
        "payment_method": "TEXT",
        "payment_name": "TEXT",
        "product_cost": "NUMERIC",
        "payment_fee": "NUMERIC DEFAULT 0",
        "promo_code": "TEXT",
        "promo_original_price": "NUMERIC",
        "promo_discount_amount": "NUMERIC DEFAULT 0",
        "customer_ip": "TEXT",
        "customer_id": "INTEGER",
        "payment_reference": "TEXT",
        "pay_code": "TEXT",
        "pay_url": "TEXT",
        "qr_url": "TEXT",
        "qr_string": "TEXT",
        "payment_expired_at": "TIMESTAMP",
        "payment_last_check_at": "TIMESTAMP",
        "payment_creation_outcome": "TEXT DEFAULT 'NOT_STARTED'",
        "tripay_payload": "TEXT",
        "provider_ref_id": "TEXT",
        "provider_rc": "TEXT",
        "provider_price": "NUMERIC",
        "provider_selling_price": "NUMERIC",
        "provider_last_balance": "NUMERIC",
        "provider_last_check_at": "TIMESTAMP",
        "provider_claim_id": "TEXT",
        "provider_claimed_at": "TIMESTAMP",
        "provider_claim_expires_at": "TIMESTAMP",
        "provider_outcome": "TEXT DEFAULT 'NOT_SENT'",
        "provider_payload": "TEXT",
        "refund_status": "TEXT",
        "refund_note": "TEXT",
        "refunded_at": "TIMESTAMP",
        "status_updated_at": "TIMESTAMP",
    }

    if str(db_engine.url).startswith("sqlite"):
        async with db_engine.begin() as conn:
            result = await conn.execute(text("PRAGMA table_info(topup)"))
            existing_columns = {row[1] for row in result.fetchall()}

            for column_name, column_type in extra_columns.items():
                if column_name not in existing_columns:
                    await conn.execute(text(f"ALTER TABLE topup ADD COLUMN {column_name} {column_type}"))
        return

    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(
                """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'topup'
            """
            )
        )
        existing_columns = {row[0] for row in result.fetchall()}

        pg_columns = {
            "order_type": "VARCHAR(50) DEFAULT 'PREPAID'",
            "price": "NUMERIC",
            "payment_method": "VARCHAR(50)",
            "payment_name": "VARCHAR(255)",
            "product_cost": "NUMERIC",
            "payment_fee": "NUMERIC DEFAULT 0",
            "promo_code": "VARCHAR(255)",
            "promo_original_price": "NUMERIC",
            "promo_discount_amount": "NUMERIC DEFAULT 0",
            "customer_ip": "VARCHAR(255)",
            "customer_id": "INTEGER",
            "payment_reference": "VARCHAR(255)",
            "pay_code": "VARCHAR(255)",
            "pay_url": "TEXT",
            "qr_url": "TEXT",
            "qr_string": "TEXT",
            "payment_expired_at": "TIMESTAMP",
            "payment_last_check_at": "TIMESTAMP",
            "payment_creation_outcome": "VARCHAR(50) DEFAULT 'NOT_STARTED'",
            "tripay_payload": "TEXT",
            "provider_ref_id": "VARCHAR(255)",
            "provider_rc": "VARCHAR(50)",
            "provider_price": "NUMERIC",
            "provider_selling_price": "NUMERIC",
            "provider_last_balance": "NUMERIC",
            "provider_last_check_at": "TIMESTAMP",
            "provider_claim_id": "VARCHAR(255)",
            "provider_claimed_at": "TIMESTAMP",
            "provider_claim_expires_at": "TIMESTAMP",
            "provider_outcome": "VARCHAR(50) DEFAULT 'NOT_SENT'",
            "provider_payload": "TEXT",
            "refund_status": "VARCHAR(50)",
            "refund_note": "TEXT",
            "refunded_at": "TIMESTAMP",
            "status_updated_at": "TIMESTAMP",
        }

        for column_name, column_type in pg_columns.items():
            if column_name not in existing_columns:
                await conn.execute(text(f"ALTER TABLE topup ADD COLUMN {column_name} {column_type}"))


async def _ensure_product_columns(db_engine: AsyncEngine) -> None:
    extra_columns = {
        "category": "TEXT",
        "product_type": "TEXT DEFAULT 'prepaid'",
        "brand": "TEXT",
        "provider_type": "TEXT",
        "buyer_product_status": "INTEGER DEFAULT 1",
        "seller_product_status": "INTEGER DEFAULT 1",
        "stock": "INTEGER",
        "unlimited_stock": "INTEGER DEFAULT 0",
        "multi": "INTEGER DEFAULT 0",
        "start_cut_off": "TEXT",
        "end_cut_off": "TEXT",
        "admin_fee": "NUMERIC DEFAULT 0",
        "commission": "NUMERIC DEFAULT 0",
        "provider_description": "TEXT",
        "description": "TEXT",
        "image_url": "TEXT",
        "logo_url": "TEXT",
        "promo_title": "TEXT",
        "promo_text": "TEXT",
        "promo_badge": "TEXT",
        "promo_url": "TEXT",
        "display_order": "INTEGER DEFAULT 0",
    }

    if str(db_engine.url).startswith("sqlite"):
        async with db_engine.begin() as conn:
            result = await conn.execute(text("PRAGMA table_info(products)"))
            existing_columns = {row[1] for row in result.fetchall()}

            for column_name, column_type in extra_columns.items():
                if column_name not in existing_columns:
                    await conn.execute(text(f"ALTER TABLE products ADD COLUMN {column_name} {column_type}"))
        return

    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(
                """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'products'
            """
            )
        )
        existing_columns = {row[0] for row in result.fetchall()}

        for column_name, column_type in extra_columns.items():
            if column_name not in existing_columns:
                await conn.execute(text(f"ALTER TABLE products ADD COLUMN {column_name} {column_type}"))


async def _ensure_promo_table(db_engine: AsyncEngine) -> None:
    if str(db_engine.url).startswith("sqlite"):
        async with db_engine.begin() as conn:
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS promos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                code TEXT,
                description TEXT,
                badge TEXT,
                cta_text TEXT,
                cta_url TEXT,
                image_url TEXT,
                rule_type TEXT DEFAULT 'content',
                target_scope TEXT DEFAULT 'all',
                target_value TEXT,
                discount_type TEXT,
                discount_value NUMERIC,
                max_discount NUMERIC,
                usage_limit INTEGER DEFAULT 0,
                payment_methods TEXT,
                budget_limit NUMERIC DEFAULT 0,
                max_per_customer INTEGER DEFAULT 0,
                max_per_phone INTEGER DEFAULT 0,
                max_per_target INTEGER DEFAULT 0,
                eligibility_rules TEXT,
                stackable INTEGER DEFAULT 1,
                priority INTEGER DEFAULT 0,
                starts_at TIMESTAMP,
                ends_at TIMESTAMP,
                show_on_website INTEGER DEFAULT 1,
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
        return

    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS promos (
            id SERIAL PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            code VARCHAR(255),
            description TEXT,
            badge VARCHAR(255),
            cta_text VARCHAR(255),
            cta_url TEXT,
            image_url TEXT,
            rule_type VARCHAR(50) DEFAULT 'content',
            target_scope VARCHAR(50) DEFAULT 'all',
            target_value TEXT,
            discount_type VARCHAR(50),
            discount_value NUMERIC,
            max_discount NUMERIC,
            usage_limit INTEGER DEFAULT 0,
            payment_methods TEXT,
            budget_limit NUMERIC DEFAULT 0,
            max_per_customer INTEGER DEFAULT 0,
            max_per_phone INTEGER DEFAULT 0,
            max_per_target INTEGER DEFAULT 0,
            eligibility_rules TEXT,
            stackable INT DEFAULT 1,
            priority INTEGER DEFAULT 0,
            starts_at TIMESTAMP,
            ends_at TIMESTAMP,
            show_on_website INT DEFAULT 1,
            active INT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )


async def _ensure_promo_columns(db_engine: AsyncEngine) -> None:
    extra_columns = {
        "rule_type": "TEXT DEFAULT 'content'",
        "target_scope": "TEXT DEFAULT 'all'",
        "target_value": "TEXT",
        "discount_type": "TEXT",
        "discount_value": "NUMERIC",
        "max_discount": "NUMERIC",
        "usage_limit": "INTEGER DEFAULT 0",
        "payment_methods": "TEXT",
        "budget_limit": "NUMERIC DEFAULT 0",
        "max_per_customer": "INTEGER DEFAULT 0",
        "max_per_phone": "INTEGER DEFAULT 0",
        "max_per_target": "INTEGER DEFAULT 0",
        "eligibility_rules": "TEXT",
        "stackable": "INTEGER DEFAULT 1",
        "priority": "INTEGER DEFAULT 0",
        "show_on_website": "INTEGER DEFAULT 1",
    }

    if str(db_engine.url).startswith("sqlite"):
        async with db_engine.begin() as conn:
            result = await conn.execute(text("PRAGMA table_info(promos)"))
            existing_columns = {row[1] for row in result.fetchall()}

            for column_name, column_type in extra_columns.items():
                if column_name not in existing_columns:
                    await conn.execute(text(f"ALTER TABLE promos ADD COLUMN {column_name} {column_type}"))
        return

    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(
                """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'promos'
            """
            )
        )
        existing_columns = {row[0] for row in result.fetchall()}

        for column_name, column_type in extra_columns.items():
            if column_name not in existing_columns:
                await conn.execute(text(f"ALTER TABLE promos ADD COLUMN {column_name} {column_type}"))


async def _ensure_admin_columns(db_engine: AsyncEngine) -> None:
    extra_columns = {
        "role": "TEXT DEFAULT 'owner'",
        "permissions": "TEXT",
        "active": "INTEGER DEFAULT 1",
        "created_at": "TIMESTAMP",
        "updated_at": "TIMESTAMP",
    }

    if str(db_engine.url).startswith("sqlite"):
        async with db_engine.begin() as conn:
            result = await conn.execute(text("PRAGMA table_info(admin)"))
            existing_columns = {row[1] for row in result.fetchall()}

            for column_name, column_type in extra_columns.items():
                if column_name not in existing_columns:
                    await conn.execute(text(f"ALTER TABLE admin ADD COLUMN {column_name} {column_type}"))

            await conn.execute(text("UPDATE admin SET role=COALESCE(NULLIF(role, ''), 'owner')"))
            await conn.execute(text("UPDATE admin SET active=1 WHERE active IS NULL"))
            await conn.execute(text("UPDATE admin SET created_at=CURRENT_TIMESTAMP WHERE created_at IS NULL"))
            await conn.execute(text("UPDATE admin SET updated_at=CURRENT_TIMESTAMP WHERE updated_at IS NULL"))
        return

    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(
                """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'admin'
            """
            )
        )
        existing_columns = {row[0] for row in result.fetchall()}

        pg_columns = {
            "role": "VARCHAR(50) DEFAULT 'owner'",
            "permissions": "TEXT",
            "active": "INT DEFAULT 1",
            "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "updated_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        }
        for column_name, column_type in pg_columns.items():
            if column_name not in existing_columns:
                await conn.execute(text(f"ALTER TABLE admin ADD COLUMN {column_name} {column_type}"))

        await conn.execute(text("UPDATE admin SET role=COALESCE(NULLIF(role, ''), 'owner')"))
        await conn.execute(text("UPDATE admin SET active=1 WHERE active IS NULL"))


async def _ensure_site_settings_table(db_engine: AsyncEngine) -> None:
    if str(db_engine.url).startswith("sqlite"):
        async with db_engine.begin() as conn:
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS site_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_by TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
        return

    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS site_settings (
            key VARCHAR(255) PRIMARY KEY,
            value TEXT,
            updated_by VARCHAR(255),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )


async def _ensure_site_pages_table(db_engine: AsyncEngine) -> None:
    if str(db_engine.url).startswith("sqlite"):
        async with db_engine.begin() as conn:
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS site_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                excerpt TEXT,
                content TEXT,
                image_url TEXT,
                page_type TEXT DEFAULT 'general',
                badge TEXT,
                cta_text TEXT,
                cta_url TEXT,
                secondary_cta_text TEXT,
                secondary_cta_url TEXT,
                promo_code TEXT,
                highlight_title TEXT,
                highlight_items TEXT,
                terms_text TEXT,
                accent_color TEXT DEFAULT 'gold',
                active INTEGER DEFAULT 1,
                show_on_website INTEGER DEFAULT 1,
                created_by TEXT,
                updated_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
        return

    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS site_pages (
            id SERIAL PRIMARY KEY,
            slug VARCHAR(255) UNIQUE NOT NULL,
            title VARCHAR(255) NOT NULL,
            excerpt TEXT,
            content TEXT,
            image_url TEXT,
            page_type VARCHAR(50) DEFAULT 'general',
            badge VARCHAR(255),
            cta_text VARCHAR(255),
            cta_url TEXT,
            secondary_cta_text VARCHAR(255),
            secondary_cta_url TEXT,
            promo_code VARCHAR(255),
            highlight_title VARCHAR(255),
            highlight_items TEXT,
            terms_text TEXT,
            accent_color VARCHAR(50) DEFAULT 'gold',
            active INT DEFAULT 1,
            show_on_website INT DEFAULT 1,
            created_by VARCHAR(255),
            updated_by VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )


async def _ensure_site_page_columns(db_engine: AsyncEngine) -> None:
    extra_columns = {
        "page_type": "TEXT DEFAULT 'general'",
        "badge": "TEXT",
        "cta_text": "TEXT",
        "cta_url": "TEXT",
        "secondary_cta_text": "TEXT",
        "secondary_cta_url": "TEXT",
        "promo_code": "TEXT",
        "highlight_title": "TEXT",
        "highlight_items": "TEXT",
        "terms_text": "TEXT",
        "accent_color": "TEXT DEFAULT 'gold'",
    }

    if str(db_engine.url).startswith("sqlite"):
        async with db_engine.begin() as conn:
            result = await conn.execute(text("PRAGMA table_info(site_pages)"))
            existing_columns = {row[1] for row in result.fetchall()}

            for column_name, column_type in extra_columns.items():
                if column_name not in existing_columns:
                    await conn.execute(text(f"ALTER TABLE site_pages ADD COLUMN {column_name} {column_type}"))

            await conn.execute(text("UPDATE site_pages SET page_type=COALESCE(NULLIF(page_type, ''), 'general')"))
            await conn.execute(text("UPDATE site_pages SET accent_color=COALESCE(NULLIF(accent_color, ''), 'gold')"))
        return

    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(
                """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'site_pages'
            """
            )
        )
        existing_columns = {row[0] for row in result.fetchall()}

        pg_columns = {
            "page_type": "VARCHAR(50) DEFAULT 'general'",
            "badge": "VARCHAR(255)",
            "cta_text": "VARCHAR(255)",
            "cta_url": "TEXT",
            "secondary_cta_text": "VARCHAR(255)",
            "secondary_cta_url": "TEXT",
            "promo_code": "VARCHAR(255)",
            "highlight_title": "VARCHAR(255)",
            "highlight_items": "TEXT",
            "terms_text": "TEXT",
            "accent_color": "VARCHAR(50) DEFAULT 'gold'",
        }

        for column_name, column_type in pg_columns.items():
            if column_name not in existing_columns:
                await conn.execute(text(f"ALTER TABLE site_pages ADD COLUMN {column_name} {column_type}"))

        await conn.execute(text("UPDATE site_pages SET page_type=COALESCE(NULLIF(page_type, ''), 'general')"))
        await conn.execute(text("UPDATE site_pages SET accent_color=COALESCE(NULLIF(accent_color, ''), 'gold')"))


async def _ensure_operational_tables(db_engine: AsyncEngine) -> None:
    if str(db_engine.url).startswith("sqlite"):
        async with db_engine.begin() as conn:
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS customer_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE,
                password TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS wallet_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER UNIQUE NOT NULL,
                balance NUMERIC DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS wallet_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                entry_type TEXT NOT NULL,
                amount NUMERIC NOT NULL,
                balance_after NUMERIC DEFAULT 0,
                reference_type TEXT,
                reference_id TEXT,
                idempotency_key TEXT,
                actor TEXT,
                note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS support_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER,
                name TEXT,
                phone TEXT,
                email TEXT,
                order_id TEXT,
                subject TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT DEFAULT 'OPEN',
                priority TEXT DEFAULT 'NORMAL',
                admin_note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS notification_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                recipient TEXT NOT NULL,
                subject TEXT,
                body TEXT,
                status TEXT DEFAULT 'QUEUED',
                reference_type TEXT,
                reference_id TEXT,
                event_key TEXT,
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent_at TIMESTAMP
            )
            """
                )
            )
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS webhook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                event_type TEXT,
                reference_id TEXT,
                signature_valid INTEGER DEFAULT 0,
                payload TEXT,
                response_status TEXT,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS provider_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                ref_id TEXT NOT NULL,
                request_payload TEXT,
                request_state TEXT NOT NULL DEFAULT 'SENDING',
                request_started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                response_at TIMESTAMP,
                provider_transaction_id TEXT,
                provider_status TEXT,
                local_outcome TEXT,
                provider_response TEXT,
                error_type TEXT,
                error_message TEXT,
                reconciled_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                key TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                reference_type TEXT,
                reference_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT,
                action TEXT NOT NULL,
                entity_type TEXT,
                entity_id TEXT,
                before_json TEXT,
                after_json TEXT,
                ip_address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS customer_blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT,
                target_id TEXT,
                reason TEXT,
                active INTEGER DEFAULT 1,
                created_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS customer_open_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                uuid TEXT,
                merchant_ref TEXT UNIQUE,
                method TEXT,
                payment_name TEXT,
                pay_code TEXT,
                qr_url TEXT,
                active INTEGER DEFAULT 1,
                payload TEXT,
                open_payment_last_check_at TIMESTAMP,
                open_payment_last_sync_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS customer_wallet_deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                provider TEXT DEFAULT 'tripay',
                reference TEXT UNIQUE,
                merchant_ref TEXT,
                amount NUMERIC DEFAULT 0,
                fee NUMERIC DEFAULT 0,
                status TEXT DEFAULT 'PENDING',
                payload TEXT,
                credited_at TIMESTAMP,
                last_check_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS order_idempotency_keys (
                key TEXT PRIMARY KEY,
                order_id TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
        return

    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS customer_accounts (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            phone VARCHAR(50) UNIQUE NOT NULL,
            email VARCHAR(255) UNIQUE,
            password VARCHAR(255) NOT NULL,
            active INT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS wallet_accounts (
            id SERIAL PRIMARY KEY,
            customer_id INTEGER UNIQUE NOT NULL,
            balance NUMERIC DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS wallet_ledger (
            id SERIAL PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            entry_type VARCHAR(50) NOT NULL,
            amount NUMERIC NOT NULL,
            balance_after NUMERIC DEFAULT 0,
            reference_type VARCHAR(100),
            reference_id VARCHAR(255),
            idempotency_key VARCHAR(255),
            actor VARCHAR(255),
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS support_tickets (
            id SERIAL PRIMARY KEY,
            customer_id INTEGER,
            name VARCHAR(255),
            phone VARCHAR(50),
            email VARCHAR(255),
            order_id VARCHAR(255),
            subject VARCHAR(255) NOT NULL,
            message TEXT NOT NULL,
            status VARCHAR(50) DEFAULT 'OPEN',
            priority VARCHAR(50) DEFAULT 'NORMAL',
            admin_note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS notification_outbox (
            id SERIAL PRIMARY KEY,
            channel VARCHAR(50) NOT NULL,
            recipient VARCHAR(255) NOT NULL,
            subject VARCHAR(255),
            body TEXT,
            status VARCHAR(50) DEFAULT 'QUEUED',
            reference_type VARCHAR(100),
            reference_id VARCHAR(255),
            event_key VARCHAR(255),
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMP
        )
        """
            )
        )
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS webhook_events (
            id SERIAL PRIMARY KEY,
            provider VARCHAR(50) NOT NULL,
            event_type VARCHAR(100),
            reference_id VARCHAR(255),
            signature_valid INT DEFAULT 0,
            payload TEXT,
            response_status VARCHAR(100),
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS provider_attempts (
            id SERIAL PRIMARY KEY,
            order_id VARCHAR(255) NOT NULL,
            provider VARCHAR(50) NOT NULL,
            attempt_number INTEGER NOT NULL,
            ref_id VARCHAR(255) NOT NULL,
            request_payload TEXT,
            request_state VARCHAR(50) NOT NULL DEFAULT 'SENDING',
            request_started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            response_at TIMESTAMP,
            provider_transaction_id VARCHAR(255),
            provider_status VARCHAR(50),
            local_outcome VARCHAR(50),
            provider_response TEXT,
            error_type VARCHAR(100),
            error_message TEXT,
            reconciled_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key VARCHAR(255) PRIMARY KEY,
            scope VARCHAR(100) NOT NULL,
            reference_type VARCHAR(100),
            reference_id VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id SERIAL PRIMARY KEY,
            actor VARCHAR(255),
            action VARCHAR(100) NOT NULL,
            entity_type VARCHAR(100),
            entity_id VARCHAR(255),
            before_json TEXT,
            after_json TEXT,
            ip_address VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS customer_blocks (
            id SERIAL PRIMARY KEY,
            phone VARCHAR(50),
            target_id VARCHAR(255),
            reason TEXT,
            active INT DEFAULT 1,
            created_by VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS customer_open_payments (
            id SERIAL PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            uuid VARCHAR(255),
            merchant_ref VARCHAR(255) UNIQUE,
            method VARCHAR(50),
            payment_name VARCHAR(255),
            pay_code VARCHAR(255),
            qr_url TEXT,
            active INT DEFAULT 1,
            payload TEXT,
            open_payment_last_check_at TIMESTAMP,
            open_payment_last_sync_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS customer_wallet_deposits (
            id SERIAL PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            provider VARCHAR(50) DEFAULT 'tripay',
            reference VARCHAR(255) UNIQUE,
            merchant_ref VARCHAR(255),
            amount NUMERIC DEFAULT 0,
            fee NUMERIC DEFAULT 0,
            status VARCHAR(50) DEFAULT 'PENDING',
            payload TEXT,
            credited_at TIMESTAMP,
            last_check_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS order_idempotency_keys (
            key VARCHAR(255) PRIMARY KEY,
            order_id VARCHAR(255) UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )


async def _ensure_operational_columns(db_engine: AsyncEngine) -> None:
    table_columns = {
        "customer_open_payments": {
            "open_payment_last_check_at": "TIMESTAMP",
            "open_payment_last_sync_message": "TEXT",
        },
        "customer_wallet_deposits": {
            "last_check_at": "TIMESTAMP",
        },
        "wallet_ledger": {
            "idempotency_key": "TEXT",
            "actor": "TEXT",
        },
        "notification_outbox": {
            "event_key": "TEXT",
        },
    }

    if str(db_engine.url).startswith("sqlite"):
        async with db_engine.begin() as conn:
            for table_name, columns in table_columns.items():
                result = await conn.execute(text(f"PRAGMA table_info({table_name})"))
                existing_columns = {row[1] for row in result.fetchall()}
                for column_name, column_type in columns.items():
                    if column_name not in existing_columns:
                        await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))
        return

    async with db_engine.begin() as conn:
        for table_name, columns in table_columns.items():
            result = await conn.execute(
                text(
                    """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = :table_name
                """
                ),
                {"table_name": table_name},
            )
            existing_columns = {row[0] for row in result.fetchall()}
            pg_columns = {
                "open_payment_last_check_at": "TIMESTAMP",
                "open_payment_last_sync_message": "TEXT",
                "last_check_at": "TIMESTAMP",
                "idempotency_key": "VARCHAR(255)",
                "actor": "VARCHAR(255)",
            }
            for column_name in columns:
                if column_name not in existing_columns:
                    await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {pg_columns[column_name]}"))


PROMOTION_MIGRATION_ID = "20260712_promotion_management_v2"
PROMOTION_ELIGIBILITY_MIGRATION_ID = "20260720_promotion_eligibility_v1"


async def _ensure_promotion_management_schema(db_engine: AsyncEngine) -> None:
    """Add the promotion v2 schema without replacing any legacy column or row."""

    sqlite = str(db_engine.url).startswith("sqlite")
    promo_columns = {
        "internal_code": "TEXT",
        "promo_type": "TEXT",
        "lifecycle_status": "TEXT",
        "rules_version": "TEXT DEFAULT 'legacy_v1'",
        "internal_description": "TEXT",
        "customer_description": "TEXT",
        "admin_notes": "TEXT",
        "calculation_type": "TEXT",
        "minimum_transaction": "NUMERIC DEFAULT 0",
        "special_price": "NUMERIC",
        "rounding_rule": "TEXT DEFAULT 'none'",
        "quota_daily": "INTEGER DEFAULT 0",
        "max_per_customer_daily": "INTEGER DEFAULT 0",
        "customer_segment": "TEXT DEFAULT 'all'",
        "eligibility_rules": "TEXT",
        "timezone": "TEXT DEFAULT 'Asia/Jakarta'",
        "active_days": "TEXT",
        "daily_start_time": "TEXT",
        "daily_end_time": "TEXT",
        "exclusive": "INTEGER DEFAULT 0",
        "max_promotions_per_order": "INTEGER DEFAULT 2",
        "placements": "TEXT",
        "display_order": "INTEGER DEFAULT 0",
        "allow_external_cta": "INTEGER DEFAULT 0",
        "legacy_compatible": "INTEGER DEFAULT 1",
        "archived_at": "TIMESTAMP",
        "paused_at": "TIMESTAMP",
        "ended_at": "TIMESTAMP",
        "created_by": "TEXT",
        "updated_by": "TEXT",
    }
    topup_columns = {
        "promo_id": "INTEGER",
        "promo_name": "TEXT",
        "promo_type": "TEXT",
        "promo_discount_type": "TEXT",
        "promo_snapshot": "TEXT",
        "promo_snapshot_version": "INTEGER DEFAULT 0",
    }

    async with db_engine.begin() as conn:
        if sqlite:
            promo_info = await conn.execute(text("PRAGMA table_info(promos)"))
            existing_promo_columns = {row[1] for row in promo_info.fetchall()}
            topup_info = await conn.execute(text("PRAGMA table_info(topup)"))
            existing_topup_columns = {row[1] for row in topup_info.fetchall()}
        else:
            promo_info = await conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name='promos'")
            )
            existing_promo_columns = {row[0] for row in promo_info.fetchall()}
            topup_info = await conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name='topup'")
            )
            existing_topup_columns = {row[0] for row in topup_info.fetchall()}

        for column_name, column_type in promo_columns.items():
            if column_name not in existing_promo_columns:
                await conn.execute(text(f"ALTER TABLE promos ADD COLUMN {column_name} {column_type}"))
        for column_name, column_type in topup_columns.items():
            if column_name not in existing_topup_columns:
                await conn.execute(text(f"ALTER TABLE topup ADD COLUMN {column_name} {column_type}"))

        serial = "INTEGER PRIMARY KEY AUTOINCREMENT" if sqlite else "SERIAL PRIMARY KEY"
        int_flag = "INTEGER" if sqlite else "INT"
        await conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS promotion_target_catalog (
                    id {serial},
                    target_type TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    label TEXT NOT NULL,
                    metadata_json TEXT,
                    active {int_flag} DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (target_type, target_key)
                )
                """
            )
        )
        await conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS promotion_targets (
                    id {serial},
                    promo_id INTEGER NOT NULL,
                    target_option_id INTEGER NOT NULL,
                    excluded {int_flag} DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (promo_id, target_option_id, excluded),
                    FOREIGN KEY (promo_id) REFERENCES promos(id),
                    FOREIGN KEY (target_option_id) REFERENCES promotion_target_catalog(id)
                )
                """
            )
        )
        await conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS promotion_customer_targets (
                    id {serial},
                    promo_id INTEGER NOT NULL,
                    customer_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (promo_id, customer_id),
                    FOREIGN KEY (promo_id) REFERENCES promos(id),
                    FOREIGN KEY (customer_id) REFERENCES customer_accounts(id)
                )
                """
            )
        )
        await conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS promotion_redemptions (
                    id {serial},
                    promo_id INTEGER NOT NULL,
                    order_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'RESERVED',
                    customer_id INTEGER,
                    customer_hash TEXT,
                    target_hash TEXT,
                    product_sku TEXT,
                    payment_method TEXT,
                    original_amount NUMERIC NOT NULL DEFAULT 0,
                    discount_amount NUMERIC NOT NULL DEFAULT 0,
                    final_amount NUMERIC NOT NULL DEFAULT 0,
                    voucher_code TEXT,
                    application_source TEXT,
                    reserved_at TIMESTAMP,
                    expires_at TIMESTAMP,
                    redeemed_at TIMESTAMP,
                    released_at TIMESTAMP,
                    snapshot_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (promo_id, order_id),
                    FOREIGN KEY (promo_id) REFERENCES promos(id),
                    FOREIGN KEY (order_id) REFERENCES topup(id)
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    migration_id TEXT PRIMARY KEY,
                    checksum TEXT,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )

        # Defaults are derived from legacy columns and never rewrite legacy values.
        await conn.execute(
            text(
                """
                UPDATE promos
                SET internal_code=COALESCE(NULLIF(TRIM(internal_code), ''), 'LEGACY-' || CAST(id AS TEXT)),
                    promo_type=COALESCE(NULLIF(TRIM(promo_type), ''),
                        CASE
                            WHEN LOWER(COALESCE(rule_type, 'content'))='content' THEN 'banner'
                            WHEN TRIM(COALESCE(code, ''))<>'' THEN 'voucher'
                            ELSE 'automatic'
                        END),
                    lifecycle_status=COALESCE(NULLIF(TRIM(lifecycle_status), ''),
                        CASE WHEN COALESCE(active, 1)=1 THEN 'active' ELSE 'disabled' END),
                    rules_version=COALESCE(NULLIF(TRIM(rules_version), ''), 'legacy_v1'),
                    customer_description=COALESCE(customer_description, description),
                    calculation_type=COALESCE(NULLIF(TRIM(calculation_type), ''), discount_type),
                    minimum_transaction=COALESCE(minimum_transaction, 0),
                    rounding_rule=COALESCE(NULLIF(TRIM(rounding_rule), ''), 'none'),
                    quota_daily=COALESCE(quota_daily, 0),
                    max_per_customer_daily=COALESCE(max_per_customer_daily, 0),
                    customer_segment=COALESCE(NULLIF(TRIM(customer_segment), ''), 'all'),
                    timezone=COALESCE(NULLIF(TRIM(timezone), ''), 'Asia/Jakarta'),
                    exclusive=COALESCE(exclusive, 0),
                    max_promotions_per_order=COALESCE(max_promotions_per_order, 2),
                    placements=COALESCE(placements, CASE WHEN COALESCE(show_on_website, 1)=1 THEN '["promo_cards"]' ELSE '[]' END),
                    display_order=COALESCE(display_order, 0),
                    allow_external_cta=COALESCE(allow_external_cta, 0),
                    legacy_compatible=COALESCE(legacy_compatible, 1)
                """
            )
        )
        await conn.execute(text("UPDATE topup SET promo_snapshot_version=COALESCE(promo_snapshot_version, 0)"))

        duplicate_vouchers = await conn.execute(
            text(
                """
                SELECT UPPER(TRIM(code))
                FROM promos
                WHERE code IS NOT NULL AND TRIM(code)<>'' AND archived_at IS NULL
                GROUP BY UPPER(TRIM(code))
                HAVING COUNT(*) > 1
                LIMIT 1
                """
            )
        )
        if duplicate_vouchers.first() is None:
            await conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_promos_voucher_code_ci
                    ON promos (UPPER(TRIM(code)))
                    WHERE code IS NOT NULL AND TRIM(code)<>'' AND archived_at IS NULL
                    """
                )
            )

        duplicate_internal = await conn.execute(
            text(
                """
                SELECT UPPER(TRIM(internal_code))
                FROM promos
                WHERE internal_code IS NOT NULL AND TRIM(internal_code)<>'' AND archived_at IS NULL
                GROUP BY UPPER(TRIM(internal_code))
                HAVING COUNT(*) > 1
                LIMIT 1
                """
            )
        )
        if duplicate_internal.first() is None:
            await conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_promos_internal_code_ci
                    ON promos (UPPER(TRIM(internal_code)))
                    WHERE internal_code IS NOT NULL AND TRIM(internal_code)<>'' AND archived_at IS NULL
                    """
                )
            )

        index_statements = [
            "CREATE INDEX IF NOT EXISTS idx_promos_lifecycle ON promos (lifecycle_status, starts_at, ends_at)",
            "CREATE INDEX IF NOT EXISTS idx_promotion_catalog_type ON promotion_target_catalog (target_type, active)",
            "CREATE INDEX IF NOT EXISTS idx_promotion_targets_promo ON promotion_targets (promo_id, excluded)",
            "CREATE INDEX IF NOT EXISTS idx_promotion_customer_targets ON promotion_customer_targets (promo_id, customer_id)",
            "CREATE INDEX IF NOT EXISTS idx_promotion_redemptions_status ON promotion_redemptions (promo_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_promotion_redemptions_order ON promotion_redemptions (order_id)",
            "CREATE INDEX IF NOT EXISTS idx_promotion_redemptions_expiry ON promotion_redemptions (status, expires_at)",
            "CREATE INDEX IF NOT EXISTS idx_promotion_redemptions_customer ON promotion_redemptions (promo_id, customer_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_promotion_redemptions_hash ON promotion_redemptions (promo_id, customer_hash, status)",
        ]
        for statement in index_statements:
            await conn.execute(text(statement))

        if sqlite:
            await conn.execute(
                text(
                    "INSERT OR IGNORE INTO schema_migrations (migration_id, checksum) VALUES (:id, :checksum)"
                ),
                {"id": PROMOTION_MIGRATION_ID, "checksum": "additive-v2"},
            )
            await conn.execute(
                text(
                    "INSERT OR IGNORE INTO schema_migrations (migration_id, checksum) VALUES (:id, :checksum)"
                ),
                {
                    "id": PROMOTION_ELIGIBILITY_MIGRATION_ID,
                    "checksum": "nullable-text-no-backfill-v1",
                },
            )
        else:
            await conn.execute(
                text(
                    """
                    INSERT INTO schema_migrations (migration_id, checksum)
                    VALUES (:id, :checksum)
                    ON CONFLICT (migration_id) DO NOTHING
                    """
                ),
                {"id": PROMOTION_MIGRATION_ID, "checksum": "additive-v2"},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO schema_migrations (migration_id, checksum)
                    VALUES (:id, :checksum)
                    ON CONFLICT (migration_id) DO NOTHING
                    """
                ),
                {
                    "id": PROMOTION_ELIGIBILITY_MIGRATION_ID,
                    "checksum": "nullable-text-no-backfill-v1",
                },
            )

    await _sync_promotion_target_catalog(db_engine)


async def _sync_promotion_target_catalog(db_engine: AsyncEngine) -> None:
    """Build stable selector IDs from real product data and map exact legacy targets."""

    sqlite = str(db_engine.url).startswith("sqlite")
    async with db_engine.begin() as conn:
        result = await conn.execute(text("SELECT sku, provider, name, category, COALESCE(active, 0) FROM products"))
        rows = result.fetchall()
        catalog: dict[tuple[str, str], tuple[str, int]] = {}
        for sku, provider, name, category, active in rows:
            if sku:
                catalog[("sku", str(sku))] = (str(name or sku), int(active or 0))
            if provider:
                catalog[("provider", str(provider))] = (str(provider), 1)
            if category:
                catalog[("category", str(category))] = (str(category), 1)

        for (target_type, target_key), (label, active) in catalog.items():
            params = {
                "target_type": target_type,
                "target_key": target_key,
                "label": label,
                "active": active,
            }
            if sqlite:
                await conn.execute(
                    text(
                        """
                        INSERT INTO promotion_target_catalog (target_type, target_key, label, active, updated_at)
                        VALUES (:target_type, :target_key, :label, :active, CURRENT_TIMESTAMP)
                        ON CONFLICT (target_type, target_key) DO UPDATE SET
                            label=excluded.label,
                            active=excluded.active,
                            updated_at=CURRENT_TIMESTAMP
                        """
                    ),
                    params,
                )
            else:
                await conn.execute(
                    text(
                        """
                        INSERT INTO promotion_target_catalog (target_type, target_key, label, active, updated_at)
                        VALUES (:target_type, :target_key, :label, :active, CURRENT_TIMESTAMP)
                        ON CONFLICT (target_type, target_key) DO UPDATE SET
                            label=EXCLUDED.label,
                            active=EXCLUDED.active,
                            updated_at=CURRENT_TIMESTAMP
                        """
                    ),
                    params,
                )

        legacy_rows = await conn.execute(
            text(
                """
                SELECT id, LOWER(COALESCE(target_scope, 'all')), COALESCE(target_value, '')
                FROM promos
                WHERE COALESCE(legacy_compatible, 1)=1
                  AND LOWER(COALESCE(target_scope, 'all')) IN ('sku', 'provider', 'category')
                """
            )
        )
        for promo_id, target_type, raw_targets in legacy_rows.fetchall():
            values = [part.strip() for part in str(raw_targets or "").replace("\n", ",").split(",") if part.strip()]
            for target_value in values:
                option = await conn.execute(
                    text(
                        """
                        SELECT id
                        FROM promotion_target_catalog
                        WHERE target_type=:target_type AND LOWER(target_key)=LOWER(:target_key)
                        ORDER BY id
                        LIMIT 1
                        """
                    ),
                    {"target_type": target_type, "target_key": target_value},
                )
                option_id = option.scalar_one_or_none()
                if option_id is None:
                    continue
                if sqlite:
                    await conn.execute(
                        text(
                            """
                            INSERT OR IGNORE INTO promotion_targets (promo_id, target_option_id, excluded)
                            VALUES (:promo_id, :option_id, 0)
                            """
                        ),
                        {"promo_id": promo_id, "option_id": option_id},
                    )
                else:
                    await conn.execute(
                        text(
                            """
                            INSERT INTO promotion_targets (promo_id, target_option_id, excluded)
                            VALUES (:promo_id, :option_id, 0)
                            ON CONFLICT (promo_id, target_option_id, excluded) DO NOTHING
                            """
                        ),
                        {"promo_id": promo_id, "option_id": option_id},
                    )


async def _ensure_operational_indexes(db_engine: AsyncEngine) -> None:
    index_statements = [
        "CREATE INDEX IF NOT EXISTS idx_topup_payment_reference ON topup (payment_reference)",
        "CREATE INDEX IF NOT EXISTS idx_topup_payment_status ON topup (payment_status, topup_status)",
        "CREATE INDEX IF NOT EXISTS idx_topup_provider_check ON topup (provider_last_check_at)",
        "CREATE INDEX IF NOT EXISTS idx_topup_payment_check ON topup (payment_last_check_at)",
        "CREATE INDEX IF NOT EXISTS idx_topup_payment_expiry ON topup (payment_expired_at)",
        "CREATE INDEX IF NOT EXISTS idx_topup_created_at ON topup (created_at)",
        "CREATE INDEX IF NOT EXISTS idx_topup_promo_code ON topup (promo_code)",
        "CREATE INDEX IF NOT EXISTS idx_topup_promo_phone ON topup (promo_code, phone)",
        "CREATE INDEX IF NOT EXISTS idx_topup_promo_target ON topup (promo_code, nominal, target_id)",
        "CREATE INDEX IF NOT EXISTS idx_topup_promo_customer ON topup (promo_code, customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_topup_history_customer ON topup (customer_id, topup_status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_topup_history_phone ON topup (phone, topup_status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_promos_code ON promos (code)",
        "CREATE INDEX IF NOT EXISTS idx_promos_priority ON promos (active, priority)",
        "CREATE INDEX IF NOT EXISTS idx_products_category_active ON products (category, active)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_deposits_reference ON customer_wallet_deposits (reference)",
        "CREATE INDEX IF NOT EXISTS idx_wallet_deposits_status ON customer_wallet_deposits (status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_customer_open_payments_ref ON customer_open_payments (merchant_ref)",
        "CREATE INDEX IF NOT EXISTS idx_customer_open_payments_check ON customer_open_payments (active, open_payment_last_check_at)",
        "CREATE INDEX IF NOT EXISTS idx_wallet_deposits_check ON customer_wallet_deposits (status, last_check_at)",
        "CREATE INDEX IF NOT EXISTS idx_webhook_events_provider_ref ON webhook_events (provider, reference_id, event_type)",
        "CREATE INDEX IF NOT EXISTS idx_notification_outbox_status ON notification_outbox (status, created_at)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_notification_event_key ON notification_outbox (event_key)",
        "CREATE INDEX IF NOT EXISTS idx_wallet_ledger_idempotency ON wallet_ledger (idempotency_key)",
        "CREATE INDEX IF NOT EXISTS idx_topup_provider_claim ON topup (topup_status, provider_claim_expires_at)",
        "CREATE INDEX IF NOT EXISTS idx_topup_provider_outcome ON topup (provider_outcome, topup_status)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_attempt_number ON provider_attempts (provider, order_id, attempt_number)",
        "CREATE INDEX IF NOT EXISTS idx_provider_attempt_ref ON provider_attempts (provider, ref_id, request_state)",
    ]
    async with db_engine.begin() as conn:
        for statement in index_statements:
            await conn.execute(text(statement))


def get_engine() -> AsyncEngine:
    global engine
    if engine is None:
        engine_options: dict[str, object] = {"echo": False, "pool_pre_ping": True}
        if settings.write_quiescence:
            # This is a connection-local PostgreSQL safety net, not a persistent
            # database setting.  Staging startup performs only SELECT 1, and any
            # accidental raw write that bypasses the application fence fails.
            engine_options["connect_args"] = {"server_settings": {"default_transaction_read_only": "on"}}
        engine = create_async_engine(get_database_url(), **engine_options)
    return engine


async def verify_database_connection() -> None:
    db_engine = get_engine()
    async with db_engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def initialize_database_runtime() -> None:
    log_database_runtime()
    if settings.allows_automatic_schema_bootstrap:
        await init_db()
        return
    await verify_database_connection()


async def init_db() -> None:
    db_engine = get_engine()

    if str(db_engine.url).startswith("sqlite"):
        async with db_engine.begin() as conn:
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS topup (
                id TEXT PRIMARY KEY,
                order_type TEXT DEFAULT 'PREPAID',
                phone TEXT,
                target_id TEXT,
                nickname TEXT,
                nominal TEXT,
                price NUMERIC,
                amount NUMERIC,
                payment_method TEXT,
                payment_name TEXT,
                payment_status TEXT,
                topup_status TEXT,
                sn TEXT,
                note TEXT,
                invoice_url TEXT,
                pay_code TEXT,
                pay_url TEXT,
                qr_url TEXT,
                qr_string TEXT,
                payment_expired_at TIMESTAMP,
                payment_last_check_at TIMESTAMP,
                payment_creation_outcome TEXT DEFAULT 'NOT_STARTED',
                tripay_payload TEXT,
                product_cost NUMERIC,
                payment_fee NUMERIC DEFAULT 0,
                promo_code TEXT,
                promo_original_price NUMERIC,
                promo_discount_amount NUMERIC DEFAULT 0,
                customer_ip TEXT,
                customer_id INTEGER,
                payment_reference TEXT,
                provider_ref_id TEXT,
                provider_rc TEXT,
                provider_price NUMERIC,
                provider_selling_price NUMERIC,
                provider_last_balance NUMERIC,
                provider_last_check_at TIMESTAMP,
                provider_claim_id TEXT,
                provider_claimed_at TIMESTAMP,
                provider_claim_expires_at TIMESTAMP,
                provider_payload TEXT,
                refund_status TEXT,
                refund_note TEXT,
                refunded_at TIMESTAMP,
                status_updated_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                provider_retry_count INTEGER DEFAULT 0,
                provider_last_error TEXT
            )
            """
                )
            )
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS admin (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT DEFAULT 'owner',
                permissions TEXT,
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS products (
                sku TEXT PRIMARY KEY,
                provider TEXT,
                name TEXT,
                cost_price NUMERIC,
                price NUMERIC,
                active INTEGER DEFAULT 0,
                category TEXT,
                product_type TEXT DEFAULT 'prepaid',
                brand TEXT,
                provider_type TEXT,
                buyer_product_status INTEGER DEFAULT 1,
                seller_product_status INTEGER DEFAULT 1,
                stock INTEGER,
                unlimited_stock INTEGER DEFAULT 0,
                multi INTEGER DEFAULT 0,
                start_cut_off TEXT,
                end_cut_off TEXT,
                admin_fee NUMERIC DEFAULT 0,
                commission NUMERIC DEFAULT 0,
                provider_description TEXT,
                description TEXT,
                image_url TEXT,
                logo_url TEXT,
                promo_title TEXT,
                promo_text TEXT,
                promo_badge TEXT,
                promo_url TEXT,
                display_order INTEGER DEFAULT 0
            )
            """
                )
            )
            await conn.execute(
                text(
                    """
            CREATE TABLE IF NOT EXISTS promos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                code TEXT,
                description TEXT,
                badge TEXT,
                cta_text TEXT,
                cta_url TEXT,
                image_url TEXT,
                rule_type TEXT DEFAULT 'content',
                target_scope TEXT DEFAULT 'all',
                target_value TEXT,
                discount_type TEXT,
                discount_value NUMERIC,
                max_discount NUMERIC,
                usage_limit INTEGER DEFAULT 0,
                payment_methods TEXT,
                budget_limit NUMERIC DEFAULT 0,
                max_per_customer INTEGER DEFAULT 0,
                max_per_phone INTEGER DEFAULT 0,
                max_per_target INTEGER DEFAULT 0,
                eligibility_rules TEXT,
                stackable INTEGER DEFAULT 1,
                priority INTEGER DEFAULT 0,
                starts_at TIMESTAMP,
                ends_at TIMESTAMP,
                show_on_website INTEGER DEFAULT 1,
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
                )
            )

            result = await conn.execute(text("SELECT COUNT(*) FROM products"))
            product_count = result.scalar_one_or_none() or 0
            if product_count == 0:
                for product in _default_products():
                    await conn.execute(
                        text(
                            """
                        INSERT OR IGNORE INTO products (sku, provider, name, cost_price, price, active, category, description, image_url, logo_url, promo_title, promo_text, promo_badge, promo_url)
                        VALUES (:sku, :provider, :name, :cost_price, :price, :active, :category, :description, :image_url, :logo_url, :promo_title, :promo_text, :promo_badge, :promo_url)
                        """
                        ),
                        product,
                    )

        await _ensure_topup_retry_columns(db_engine)
        await _ensure_topup_business_columns(db_engine)
        await _ensure_product_columns(db_engine)
        await _ensure_promo_table(db_engine)
        await _ensure_promo_columns(db_engine)
        await _ensure_admin_columns(db_engine)
        await _ensure_site_settings_table(db_engine)
        await _ensure_site_pages_table(db_engine)
        await _ensure_site_page_columns(db_engine)
        await _ensure_operational_tables(db_engine)
        await _ensure_operational_columns(db_engine)
        await _ensure_promotion_management_schema(db_engine)
        await _ensure_operational_indexes(db_engine)
        return

    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS topup (
            id VARCHAR(255) PRIMARY KEY,
            order_type VARCHAR(50) DEFAULT 'PREPAID',
            phone VARCHAR(50),
            target_id VARCHAR(255),
            nickname VARCHAR(255),
            nominal VARCHAR(100),
            price NUMERIC,
            amount NUMERIC,
            payment_method VARCHAR(50),
            payment_name VARCHAR(255),
            payment_status VARCHAR(50),
            topup_status VARCHAR(50),
            sn VARCHAR(255),
            note TEXT,
            invoice_url TEXT,
            pay_code VARCHAR(255),
            pay_url TEXT,
            qr_url TEXT,
            qr_string TEXT,
            payment_expired_at TIMESTAMP,
            payment_last_check_at TIMESTAMP,
            payment_creation_outcome VARCHAR(50) DEFAULT 'NOT_STARTED',
            tripay_payload TEXT,
            product_cost NUMERIC,
            payment_fee NUMERIC DEFAULT 0,
            promo_code VARCHAR(255),
            promo_original_price NUMERIC,
            promo_discount_amount NUMERIC DEFAULT 0,
            customer_ip VARCHAR(255),
            customer_id INTEGER,
            payment_reference VARCHAR(255),
            provider_ref_id VARCHAR(255),
            provider_rc VARCHAR(50),
            provider_price NUMERIC,
            provider_selling_price NUMERIC,
            provider_last_balance NUMERIC,
            provider_last_check_at TIMESTAMP,
            provider_claim_id VARCHAR(255),
            provider_claimed_at TIMESTAMP,
            provider_claim_expires_at TIMESTAMP,
            provider_payload TEXT,
            refund_status VARCHAR(50),
            refund_note TEXT,
            refunded_at TIMESTAMP,
            status_updated_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            provider_retry_count INTEGER DEFAULT 0,
            provider_last_error TEXT
        )
        """
            )
        )
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS admin (
            id SERIAL PRIMARY KEY,
            username VARCHAR(255) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            role VARCHAR(50) DEFAULT 'owner',
            permissions TEXT,
            active INT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS products (
            sku VARCHAR(255) PRIMARY KEY,
            provider VARCHAR(255),
            name VARCHAR(255),
            cost_price NUMERIC,
            price NUMERIC,
            active INT DEFAULT 0,
            category VARCHAR(100),
            product_type VARCHAR(50) DEFAULT 'prepaid',
            brand VARCHAR(255),
            provider_type VARCHAR(100),
            buyer_product_status INT DEFAULT 1,
            seller_product_status INT DEFAULT 1,
            stock INTEGER,
            unlimited_stock INT DEFAULT 0,
            multi INT DEFAULT 0,
            start_cut_off VARCHAR(100),
            end_cut_off VARCHAR(100),
            admin_fee NUMERIC DEFAULT 0,
            commission NUMERIC DEFAULT 0,
            provider_description TEXT,
            description TEXT,
            image_url TEXT,
            logo_url TEXT,
            promo_title TEXT,
            promo_text TEXT,
            promo_badge TEXT,
            promo_url TEXT,
            display_order INT DEFAULT 0
        )
        """
            )
        )
        await conn.execute(
            text(
                """
        CREATE TABLE IF NOT EXISTS promos (
            id SERIAL PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            code VARCHAR(255),
            description TEXT,
            badge VARCHAR(255),
            cta_text VARCHAR(255),
            cta_url TEXT,
            image_url TEXT,
            rule_type VARCHAR(50) DEFAULT 'content',
            target_scope VARCHAR(50) DEFAULT 'all',
            target_value TEXT,
            discount_type VARCHAR(50),
            discount_value NUMERIC,
            max_discount NUMERIC,
            usage_limit INTEGER DEFAULT 0,
            payment_methods TEXT,
            budget_limit NUMERIC DEFAULT 0,
            max_per_customer INTEGER DEFAULT 0,
            max_per_phone INTEGER DEFAULT 0,
            max_per_target INTEGER DEFAULT 0,
            eligibility_rules TEXT,
            stackable INT DEFAULT 1,
            priority INTEGER DEFAULT 0,
            starts_at TIMESTAMP,
            ends_at TIMESTAMP,
            show_on_website INT DEFAULT 1,
            active INT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
            )
        )

        result = await conn.execute(text("SELECT COUNT(*) FROM products"))
        product_count = result.scalar_one_or_none() or 0
        if product_count == 0:
            for product in _default_products():
                await conn.execute(
                    text(
                        """
                    INSERT INTO products (sku, provider, name, cost_price, price, active, category, description, image_url, logo_url, promo_title, promo_text, promo_badge, promo_url)
                    VALUES (:sku, :provider, :name, :cost_price, :price, :active, :category, :description, :image_url, :logo_url, :promo_title, :promo_text, :promo_badge, :promo_url)
                    ON CONFLICT (sku) DO NOTHING
                    """
                    ),
                    product,
                )

    await _ensure_topup_retry_columns(db_engine)
    await _ensure_topup_business_columns(db_engine)
    await _ensure_product_columns(db_engine)
    await _ensure_promo_table(db_engine)
    await _ensure_promo_columns(db_engine)
    await _ensure_admin_columns(db_engine)
    await _ensure_site_settings_table(db_engine)
    await _ensure_site_pages_table(db_engine)
    await _ensure_site_page_columns(db_engine)
    await _ensure_operational_tables(db_engine)
    await _ensure_operational_columns(db_engine)
    await _ensure_promotion_management_schema(db_engine)
    await _ensure_operational_indexes(db_engine)
