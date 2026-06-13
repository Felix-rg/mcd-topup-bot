import logging
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from config import DATABASE_URL

# Membuat Async Engine
engine = create_async_engine(
    DATABASE_URL, 
    echo=False, 
    pool_size=20, 
    max_overflow=10
)

async def db_execute(query: str, params: dict = None):
    try:
        async with engine.begin() as conn:
            await conn.execute(text(query), params or {})
    except Exception as e:
        logging.error(f"DB Execute Error: {e}")
        raise e

async def db_query(query: str, params: dict = None):
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(query), params or {})
            return [tuple(row) for row in result.fetchall()]
    except Exception as e:
        logging.error(f"DB Query Error: {e}")
        return []

async def init_db():
    # Pisahkan setiap query menjadi variabel mandiri
    tabel_topup = """
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    
    tabel_admin = """
    CREATE TABLE IF NOT EXISTS admin (
        id SERIAL PRIMARY KEY,
        username VARCHAR(255) UNIQUE NOT NULL,
        password VARCHAR(255) NOT NULL
    )
    """
    
    tabel_products = """
    CREATE TABLE IF NOT EXISTS products (
        sku VARCHAR(255) PRIMARY KEY,
        provider VARCHAR(255),
        name VARCHAR(255),
        cost_price NUMERIC,
        price NUMERIC,
        active INT DEFAULT 0,
        category VARCHAR(100)
    )
    """
    
    # Eksekusi satu per satu secara berurutan
    async with engine.begin() as conn:
        await conn.execute(text(tabel_topup))
        await conn.execute(text(tabel_admin))
        await conn.execute(text(tabel_products))
        
    print("✅ Database PostgreSQL & Tabel berhasil disiapkan!")