from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import FileResponse

from config import ADMIN_SECRET
from database import db_execute, db_query
from utils import verify_password
from models import AdminLogin

from services.digiflazz_service import get_digiflazz_products
from pydantic import BaseModel

router = APIRouter()

def verify_admin(token: str = Header(None)):
    if token != ADMIN_SECRET:
        raise HTTPException(403, "Unauthorized")

@router.get("/admin")
def admin_page():
    return FileResponse("web/admin.html")

@router.post("/admin/login")
def admin_login(data: AdminLogin):
    row = db_query("SELECT id, password FROM admin WHERE username=?", (data.username,))
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
def admin_orders(admin=Depends(verify_admin)):
    rows = db_query("""
        SELECT id, phone, nominal, payment_status, topup_status, created_at 
        FROM topup ORDER BY created_at DESC
    """)
    return [{"id": r[0], "phone": r[1], "nominal": r[2], "payment_status": r[3], "topup_status": r[4], "created_at": r[5]} for r in rows]

# ===== BAGIAN STATISTIK (Sudah menggunakan p.sku = t.nominal dan Waktu WIB) =====

@router.get("/admin/api/revenue-today")
def revenue_today(admin=Depends(verify_admin)):
    rows = db_query("""
        SELECT p.price FROM topup t
        JOIN products p ON p.sku = t.nominal
        WHERE t.topup_status='SUCCESS' AND DATE(t.created_at) = DATE('now', '+7 hours')
    """)
    return {"revenue": sum(r[0] for r in rows), "count": len(rows)}

@router.get("/admin/api/revenue-total")
def revenue_total(admin=Depends(verify_admin)):
    rows = db_query("""
        SELECT p.price FROM topup t
        JOIN products p ON p.sku = t.nominal
        WHERE t.topup_status='SUCCESS'
    """)
    return {"revenue": sum(r[0] for r in rows)}

@router.get("/admin/api/profit-today")
def profit_today(admin=Depends(verify_admin)):
    rows = db_query("""
        SELECT p.price, p.cost_price FROM topup t
        JOIN products p ON p.sku = t.nominal
        WHERE t.topup_status='SUCCESS' AND DATE(t.created_at) = DATE('now', '+7 hours')
    """)
    # r[0] adalah price, r[1] adalah cost
    return {"profit": sum((r[0] - r[1]) for r in rows)}

@router.get("/admin/api/profit-total")
def profit_total(admin=Depends(verify_admin)):
    rows = db_query("""
        SELECT p.price, p.cost_price FROM topup t
        JOIN products p ON p.sku = t.nominal
        WHERE t.topup_status='SUCCESS'
    """)
    return {"profit": sum((r[0] - r[1]) for r in rows)}

# ===== MANAJEMEN PRODUK =====

@router.get("/admin/api/products")
def get_products(admin=Depends(verify_admin)):
    # Ambil semua data, termasuk kolom category
    rows = db_query("SELECT sku, provider, name, cost_price, price, active, category FROM products ORDER BY provider, price")
    
    grouped = {}
    for r in rows:
        sku, provider, name, cost, price, active, category = r
        # Kategorikan otomatis kalau kosong
        cat_name = category if category else "Game"
        
        if cat_name not in grouped:
            grouped[cat_name] = {}
        if provider not in grouped[cat_name]:
            grouped[cat_name][provider] = []
            
        grouped[cat_name][provider].append({
            "sku": sku,
            "provider": provider,
            "name": name,
            "cost": cost,
            "price": price,
            "active": active,
            "profit": price - cost
        })
    return grouped

@router.post("/admin/api/products")
def create_product(data: dict, admin=Depends(verify_admin)):
    provider = data.get("provider")
    name = data.get("name")
    sku = data.get("sku")
    cost = data.get("cost")
    price = data.get("price")
    
    if not provider or not name or not sku:
        return {"error": "Semua field wajib diisi"}
        
    db_execute(
        "INSERT INTO products (provider, name, sku, cost, price, active) VALUES (?, ?, ?, ?, ?, 1)",
        (provider, name, sku, cost, price)
    )
    return {"message": "Produk ditambahkan"}

@router.put("/admin/api/products/{sku}/toggle")
def toggle_product(sku: str, admin=Depends(verify_admin)):
    row = db_query("SELECT active FROM products WHERE sku=?", (sku,))
    if not row:
        return {"error": "Produk tidak ditemukan"}
    
    new_status = 0 if row[0][0] == 1 else 1
    db_execute("UPDATE products SET active=? WHERE sku=?", (new_status, sku))
    return {"message": "Status produk diperbarui", "active": new_status}

@router.put("/admin/api/products/{sku}")
def update_product(sku: str, data: dict, admin=Depends(verify_admin)):
    price = data.get("price")
    cost = data.get("cost")
    
    if price is None or cost is None:
        return {"error": "Harga jual dan modal wajib diisi"}
        
    db_execute(
        "UPDATE products SET price=?, cost=? WHERE sku=?",
        (price, cost, sku)
    )
    return {"message": "Produk berhasil diperbarui"}

@router.delete("/admin/api/products/{sku}")
def delete_product(sku: str, admin=Depends(verify_admin)):
    # PERINGATAN: Menghapus produk bisa merusak riwayat laporan keuangan
    db_execute("DELETE FROM products WHERE sku=?", (sku,))
    return {"message": "Produk berhasil dihapus"}

@router.post("/admin/sync-products")
def sync_products(admin=Depends(verify_admin)):
    from services.digiflazz_service import get_digiflazz_products
    
    products = get_digiflazz_products()

    # 🛡️ PAGAR PENGAMAN: Cek apakah products itu LIST atau cuma TEKS ERROR
    if isinstance(products, str):
        # Kalau dapetnya teks error dari Digiflazz
        print(f"🚨 GAGAL SINKRON: {products}")
        return {"message": f"Gagal: {products}"}
    
    if not products or not isinstance(products, list):
        return {"message": "Gagal: Data Digiflazz kosong atau salah format."}

    count = 0
    for p in products:
        # Cek lagi buat mastiin p itu dictionary, biar gak 'str object' error lagi
        if not isinstance(p, dict): continue

        if p.get('buyer_product_status') == True:
            # --- DETEKSI KATEGORI OTOMATIS (Sangat Teliti) ---
            d_cat = p.get('category', '').lower()
            d_brand = p.get('brand', '').lower()
            
            target_category = "Lainnya" # Default

            # A. Cek Pulsa & Data (Termasuk Masa Aktif)
            if any(x in d_brand for x in ['telkomsel', 'xl', 'axis', 'indosat', 'tri', 'smartfren']) or \
               any(x in d_cat for x in ['pulsa', 'data', 'paket', 'internet', 'masa aktif']):
                target_category = "Pulsa"
            
            # B. Cek E-Wallet
            elif any(x in d_brand for x in ['dana', 'ovo', 'gopay', 'go-pay', 'shopeepay', 'linkaja', 'maxim', 'grab']) or \
                 any(x in d_cat for x in ['e-money', 'wallet']):
                target_category = "E-Wallet"
            
            # C. Cek Game
            elif any(x in d_cat for x in ['game', 'vouchers', 'vaucher']) or \
                 any(x in d_brand for x in ['mobile legends', 'free fire', 'ff', 'pubg', 'genshin', 'valorant', 'steam']):
                target_category = "Game"

            # --- INSERT/UPDATE DATABASE ---
            db_execute("""
                INSERT INTO products (sku, provider, name, price, cost_price, active, category)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(sku) DO UPDATE SET
                cost_price = excluded.cost_price,
                price = excluded.price,
                name = excluded.name,
                category = excluded.category -- WAJIB ADA BIAR INDOSAT PINDAH LACI
            """, (
                p['buyer_sku_code'],
                p['brand'],
                p['product_name'],
                int(p['price']) + 2000, 
                int(p['price']),
                target_category
            ))
            count += 1
            
    return {"message": f"Berhasil sinkron {count} produk!"}

class BulkMarkupRequest(BaseModel):
    brand: str
    percent: float
    min_profit: int

@router.post("/admin/bulk-markup")
def bulk_markup(req: BulkMarkupRequest, admin=Depends(verify_admin)):
    brand = req.brand.upper()
    # Mengubah persen jadi desimal perkalian (misal 5% jadi 0.05)
    multiplier = req.percent / 100.0 
    min_profit = req.min_profit

    try:
        # Rumus pintar SQLite: harga baru = cost_price + Nilai Terbesar antara (cost_price * desimal) ATAU min_profit
        if brand == "ALL":
            db_execute("""
                UPDATE products 
                SET price = cost_price + MAX(CAST(cost_price * ? AS INT), ?)
            """, (multiplier, min_profit))
            pesan = f"Sukses! Semua produk berhasil di-markup {req.percent}% (Minimal profit Rp {min_profit})"
        else:
            db_execute("""
                UPDATE products 
                SET price = cost_price + MAX(CAST(cost_price * ? AS INT), ?) 
                WHERE UPPER(provider) LIKE ?
            """, (multiplier, min_profit, f"%{brand}%"))
            pesan = f"Sukses! Kategori {brand} berhasil di-markup {req.percent}% (Minimal profit Rp {min_profit})"

        return {"message": pesan}
    except Exception as e:
        print(f"🚨 ERROR BULK MARKUP: {e}")
        return {"error": str(e)}