from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import FileResponse
from config import ADMIN_SECRET
from database import db_execute, db_query
from utils import verify_password
from models import AdminLogin
from pydantic import BaseModel

router = APIRouter()

def verify_admin(token: str = Header(None)):
    if token != ADMIN_SECRET:
        raise HTTPException(403, "Unauthorized")

@router.get("/admin")
def admin_page():
    return FileResponse("web/admin.html")

@router.post("/admin/login")
async def admin_login(data: AdminLogin):
    row = await db_query("SELECT id, password FROM admin WHERE username=:username", {"username": data.username})
    if not row:
        raise HTTPException(status_code=401, detail="Login gagal")
    
    admin_id, hashed_password = row[0]
    if not verify_password(data.password, hashed_password):
        raise HTTPException(status_code=401, detail="Password salah")
    
    return {"message": "Login berhasil", "token": ADMIN_SECRET}

@router.get("/admin-dashboard")
def admin_dashboard():
    return FileResponse("web/admin-dashboard.html")

@router.get("/admin/api/orders")
async def admin_orders(admin=Depends(verify_admin)):
    rows = await db_query("""
        SELECT id, phone, nominal, payment_status, topup_status, created_at 
        FROM topup ORDER BY created_at DESC
    """)
    return [{"id": r[0], "phone": r[1], "nominal": r[2], "payment_status": r[3], "topup_status": r[4], "created_at": r[5]} for r in rows]

# ===== BAGIAN STATISTIK (WIB Timezone Mode PostgreSQL) =====

@router.get("/admin/api/revenue-today")
async def revenue_today(admin=Depends(verify_admin)):
    rows = await db_query("""
        SELECT p.price FROM topup t
        JOIN products p ON p.sku = t.nominal
        WHERE t.topup_status='SUCCESS' 
        AND DATE(t.created_at) = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC' + INTERVAL '7 hours')::date
    """)
    return {"revenue": sum(r[0] for r in rows), "count": len(rows)}

@router.get("/admin/api/revenue-total")
async def revenue_total(admin=Depends(verify_admin)):
    rows = await db_query("SELECT p.price FROM topup t JOIN products p ON p.sku = t.nominal WHERE t.topup_status='SUCCESS'")
    return {"revenue": sum(r[0] for r in rows)}

@router.get("/admin/api/profit-today")
async def profit_today(admin=Depends(verify_admin)):
    rows = await db_query("""
        SELECT p.price, p.cost_price FROM topup t
        JOIN products p ON p.sku = t.nominal
        WHERE t.topup_status='SUCCESS' 
        AND DATE(t.created_at) = (CURRENT_TIMESTAMP AT TIME ZONE 'UTC' + INTERVAL '7 hours')::date
    """)
    return {"profit": sum((r[0] - r[1]) for r in rows)}

@router.get("/admin/api/profit-total")
async def profit_total(admin=Depends(verify_admin)):
    rows = await db_query("SELECT p.price, p.cost_price FROM topup t JOIN products p ON p.sku = t.nominal WHERE t.topup_status='SUCCESS'")
    return {"profit": sum((r[0] - r[1]) for r in rows)}

# ===== MANAJEMEN PRODUK =====

@router.get("/admin/api/products")
async def get_products(admin=Depends(verify_admin)):
    rows = await db_query("SELECT sku, provider, name, cost_price, price, active, category FROM products ORDER BY provider, price")
    grouped = {}
    for r in rows:
        sku, provider, name, cost, price, active, category = r
        cat_name = category if category else "Game"
        if cat_name not in grouped:
            grouped[cat_name] = {}
        if provider not in grouped[cat_name]:
            grouped[cat_name][provider] = []
        grouped[cat_name][provider].append({
            "sku": sku, "provider": provider, "name": name,
            "cost": cost, "price": price, "active": active, "profit": price - cost
        })
    return grouped

@router.post("/admin/api/products")
async def create_product(data: dict, admin=Depends(verify_admin)):
    provider, name, sku, cost, price = data.get("provider"), data.get("name"), data.get("sku"), data.get("cost"), data.get("price")
    if not provider or not name or not sku:
        return {"error": "Semua field wajib diisi"}
        
    await db_execute(
        "INSERT INTO products (provider, name, sku, cost_price, price, active) VALUES (:provider, :name, :sku, :cost, :price, 1)",
        {"provider": provider, "name": name, "sku": sku, "cost": cost, "price": price}
    )
    return {"message": "Produk ditambahkan"}

@router.put("/admin/api/products/{sku}/toggle")
async def toggle_product(sku: str, admin=Depends(verify_admin)):
    row = await db_query("SELECT active FROM products WHERE sku=:sku", {"sku": sku})
    if not row:
        return {"error": "Produk tidak ditemukan"}
    
    new_status = 0 if row[0][0] == 1 else 1
    await db_execute("UPDATE products SET active=:active WHERE sku=:sku", {"active": new_status, "sku": sku})
    return {"message": "Status produk diperbarui", "active": new_status}

@router.put("/admin/api/products/{sku}")
async def update_product(sku: str, data: dict, admin=Depends(verify_admin)):
    price, cost = data.get("price"), data.get("cost")
    if price is None or cost is None:
        return {"error": "Harga jual dan modal wajib diisi"}
        
    await db_execute(
        "UPDATE products SET price=:price, cost_price=:cost WHERE sku=:sku",
        {"price": price, "cost": cost, "sku": sku}
    )
    return {"message": "Produk berhasil diperbarui"}

@router.delete("/admin/api/products/{sku}")
async def delete_product(sku: str, admin=Depends(verify_admin)):
    await db_execute("DELETE FROM products WHERE sku=:sku", {"sku": sku})
    return {"message": "Produk berhasil dihapus"}

@router.post("/admin/sync-products")
async def sync_products(admin=Depends(verify_admin)):
    from services.digiflazz_service import get_digiflazz_products
    
    # WAJIB AWAIT
    products = await get_digiflazz_products()

    if isinstance(products, str):
        print(f"🚨 GAGAL SINKRON: {products}")
        return {"message": f"Gagal: {products}"}
    
    if not products or not isinstance(products, list):
        return {"message": "Gagal: Data Digiflazz kosong atau salah format."}

    count = 0
    for p in products:
        if not isinstance(p, dict): continue

        if p.get('buyer_product_status') == True:
            d_cat, d_brand = p.get('category', '').lower(), p.get('brand', '').lower()
            target_category = "Lainnya"

            if any(x in d_brand for x in ['telkomsel', 'xl', 'axis', 'indosat', 'tri', 'smartfren']) or \
               any(x in d_cat for x in ['pulsa', 'data', 'paket', 'internet', 'masa aktif']):
                target_category = "Pulsa"
            elif any(x in d_brand for x in ['dana', 'ovo', 'gopay', 'go-pay', 'shopeepay', 'linkaja', 'maxim', 'grab']) or \
                 any(x in d_cat for x in ['e-money', 'wallet']):
                target_category = "E-Wallet"
            elif any(x in d_cat for x in ['game', 'vouchers', 'vaucher']) or \
                 any(x in d_brand for x in ['mobile legends', 'free fire', 'ff', 'pubg', 'genshin', 'valorant', 'steam']):
                target_category = "Game"

            # POSTGRESQL UPSERT SYNTAX
            await db_execute("""
                INSERT INTO products (sku, provider, name, price, cost_price, active, category)
                VALUES (:sku, :brand, :name, :price, :cost, 0, :category)
                ON CONFLICT(sku) DO UPDATE SET
                cost_price = EXCLUDED.cost_price,
                price = EXCLUDED.price,
                name = EXCLUDED.name,
                category = EXCLUDED.category
            """, {
                "sku": p['buyer_sku_code'],
                "brand": p['brand'],
                "name": p['product_name'],
                "price": int(p['price']) + 2000, 
                "cost": int(p['price']),
                "category": target_category
            })
            count += 1
            
    return {"message": f"Berhasil sinkron {count} produk!"}

class BulkMarkupRequest(BaseModel):
    brand: str
    percent: float
    min_profit: int

@router.post("/admin/bulk-markup")
async def bulk_markup(req: BulkMarkupRequest, admin=Depends(verify_admin)):
    brand = req.brand.upper()
    multiplier = req.percent / 100.0 
    min_profit = req.min_profit

    try:
        # PERBAIKAN: PostgreSQL menggunakan GREATEST, bukan MAX
        if brand == "ALL":
            await db_execute("""
                UPDATE products 
                SET price = cost_price + GREATEST((cost_price * :multi)::int, :min_profit)
            """, {"multi": multiplier, "min_profit": min_profit})
            pesan = f"Sukses! Semua produk berhasil di-markup {req.percent}%"
        else:
            await db_execute("""
                UPDATE products 
                SET price = cost_price + GREATEST((cost_price * :multi)::int, :min_profit)
                WHERE UPPER(provider) LIKE :brand
            """, {"multi": multiplier, "min_profit": min_profit, "brand": f"%{brand}%"})
            pesan = f"Sukses! Kategori {brand} berhasil di-markup {req.percent}%"

        return {"message": pesan}
    except Exception as e:
        print(f"🚨 ERROR BULK MARKUP: {e}")
        return {"error": str(e)}