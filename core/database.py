import os
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


DEFAULT_SQLITE_URL = "sqlite+aiosqlite:///./lixafa.db"


def get_database_url() -> str:
    configured_url = os.getenv("DATABASE_URL") or os.getenv("DB_URL")
    if configured_url:
        return configured_url

    if os.getenv("POSTGRES_HOST") and os.getenv("POSTGRES_DB"):
        user = os.getenv("POSTGRES_USER", "postgres")
        password = os.getenv("POSTGRES_PASSWORD", "postgres")
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        db_name = os.getenv("POSTGRES_DB")
        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db_name}"

    return DEFAULT_SQLITE_URL


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
        result = await conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'topup'
        """))
        existing_columns = {row[0] for row in result.fetchall()}

        if "provider_retry_count" not in existing_columns:
            await conn.execute(text("ALTER TABLE topup ADD COLUMN provider_retry_count INTEGER DEFAULT 0"))
        if "provider_last_error" not in existing_columns:
            await conn.execute(text("ALTER TABLE topup ADD COLUMN provider_last_error TEXT"))


async def _ensure_product_columns(db_engine: AsyncEngine) -> None:
    extra_columns = {
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
        result = await conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'products'
        """))
        existing_columns = {row[0] for row in result.fetchall()}

        for column_name, column_type in extra_columns.items():
            if column_name not in existing_columns:
                await conn.execute(text(f"ALTER TABLE products ADD COLUMN {column_name} {column_type}"))


async def _ensure_promo_table(db_engine: AsyncEngine) -> None:
    if str(db_engine.url).startswith("sqlite"):
        async with db_engine.begin() as conn:
            await conn.execute(text("""
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
                starts_at TIMESTAMP,
                ends_at TIMESTAMP,
                show_on_website INTEGER DEFAULT 1,
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """))
        return

    async with db_engine.begin() as conn:
        await conn.execute(text("""
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
            starts_at TIMESTAMP,
            ends_at TIMESTAMP,
            show_on_website INT DEFAULT 1,
            active INT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """))


async def _ensure_promo_columns(db_engine: AsyncEngine) -> None:
    extra_columns = {
        "rule_type": "TEXT DEFAULT 'content'",
        "target_scope": "TEXT DEFAULT 'all'",
        "target_value": "TEXT",
        "discount_type": "TEXT",
        "discount_value": "NUMERIC",
        "max_discount": "NUMERIC",
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
        result = await conn.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'promos'
        """))
        existing_columns = {row[0] for row in result.fetchall()}

        for column_name, column_type in extra_columns.items():
            if column_name not in existing_columns:
                await conn.execute(text(f"ALTER TABLE promos ADD COLUMN {column_name} {column_type}"))


def get_engine() -> AsyncEngine:
    global engine
    if engine is None:
        engine = create_async_engine(get_database_url(), echo=False, pool_pre_ping=True)
    return engine


async def init_db() -> None:
    db_engine = get_engine()

    if str(db_engine.url).startswith("sqlite"):
        async with db_engine.begin() as conn:
            await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS topup (
                id TEXT PRIMARY KEY,
                phone TEXT,
                target_id TEXT,
                nickname TEXT,
                nominal TEXT,
                price NUMERIC,
                amount NUMERIC,
                payment_method TEXT,
                payment_status TEXT,
                topup_status TEXT,
                sn TEXT,
                note TEXT,
                invoice_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                provider_retry_count INTEGER DEFAULT 0,
                provider_last_error TEXT
            )
            """))
            await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS admin (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
            """))
            await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS products (
                sku TEXT PRIMARY KEY,
                provider TEXT,
                name TEXT,
                cost_price NUMERIC,
                price NUMERIC,
                active INTEGER DEFAULT 0,
                category TEXT,
                description TEXT,
                image_url TEXT,
                logo_url TEXT,
                promo_title TEXT,
                promo_text TEXT,
                promo_badge TEXT,
                promo_url TEXT,
                display_order INTEGER DEFAULT 0
            )
            """))
            await conn.execute(text("""
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
                starts_at TIMESTAMP,
                ends_at TIMESTAMP,
                show_on_website INTEGER DEFAULT 1,
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """))

            result = await conn.execute(text("SELECT COUNT(*) FROM products"))
            product_count = result.scalar_one_or_none() or 0
            if product_count == 0:
                for product in _default_products():
                    await conn.execute(
                        text("""
                        INSERT OR IGNORE INTO products (sku, provider, name, cost_price, price, active, category, description, image_url, logo_url, promo_title, promo_text, promo_badge, promo_url)
                        VALUES (:sku, :provider, :name, :cost_price, :price, :active, :category, :description, :image_url, :logo_url, :promo_title, :promo_text, :promo_badge, :promo_url)
                        """),
                        product,
                    )

        await _ensure_topup_retry_columns(db_engine)
        await _ensure_product_columns(db_engine)
        await _ensure_promo_table(db_engine)
        await _ensure_promo_columns(db_engine)
        return

    async with db_engine.begin() as conn:
        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS topup (
            id VARCHAR(255) PRIMARY KEY,
            phone VARCHAR(50),
            target_id VARCHAR(255),
            nickname VARCHAR(255),
            nominal VARCHAR(100),
            price NUMERIC,
            amount NUMERIC,
            payment_method VARCHAR(50),
            payment_status VARCHAR(50),
            topup_status VARCHAR(50),
            sn VARCHAR(255),
            note TEXT,
            invoice_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            provider_retry_count INTEGER DEFAULT 0,
            provider_last_error TEXT
        )
        """))
        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS admin (
            id SERIAL PRIMARY KEY,
            username VARCHAR(255) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL
        )
        """))
        await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS products (
            sku VARCHAR(255) PRIMARY KEY,
            provider VARCHAR(255),
            name VARCHAR(255),
            cost_price NUMERIC,
            price NUMERIC,
            active INT DEFAULT 0,
            category VARCHAR(100),
            description TEXT,
            image_url TEXT,
            logo_url TEXT,
            promo_title TEXT,
            promo_text TEXT,
            promo_badge TEXT,
            promo_url TEXT,
            display_order INT DEFAULT 0
        )
        """))
        await conn.execute(text("""
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
            starts_at TIMESTAMP,
            ends_at TIMESTAMP,
            show_on_website INT DEFAULT 1,
            active INT DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """))

        result = await conn.execute(text("SELECT COUNT(*) FROM products"))
        product_count = result.scalar_one_or_none() or 0
        if product_count == 0:
            for product in _default_products():
                await conn.execute(
                    text("""
                    INSERT INTO products (sku, provider, name, cost_price, price, active, category, description, image_url, logo_url, promo_title, promo_text, promo_badge, promo_url)
                    VALUES (:sku, :provider, :name, :cost_price, :price, :active, :category, :description, :image_url, :logo_url, :promo_title, :promo_text, :promo_badge, :promo_url)
                    ON CONFLICT (sku) DO NOTHING
                    """),
                    product,
                )

    await _ensure_topup_retry_columns(db_engine)
    await _ensure_product_columns(db_engine)
    await _ensure_promo_table(db_engine)
    await _ensure_promo_columns(db_engine)
