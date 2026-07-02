from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.database import db_execute, db_query
from app.security import create_access_token, decode_access_token, verify_password
from app.services.digiflazz_service import get_digiflazz_products
from app.services.product_utils import normalized_category_name, normalized_provider_name
from app.utils import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminLoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6)


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    username: str
    token: Optional[str] = None
    message: Optional[str] = None


class ProductCreateRequest(BaseModel):
    sku: Optional[str] = None
    provider: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    cost_price: Optional[float] = None
    cost: Optional[float] = None
    price: float = Field(..., ge=0)
    category: str = Field(default="Lainnya")
    description: Optional[str] = None
    image_url: Optional[str] = None
    logo_url: Optional[str] = None
    promo_title: Optional[str] = None
    promo_text: Optional[str] = None
    promo_badge: Optional[str] = None
    promo_url: Optional[str] = None
    active: int = Field(default=1, ge=0, le=1)
    display_order: int = Field(default=0)

    @field_validator("price")
    @classmethod
    def price_must_exceed_cost(cls, value, info):
        cost = info.data.get("cost_price") or info.data.get("cost")
        if cost is not None and value < cost:
            raise ValueError("Harga jual tidak boleh lebih rendah dari harga modal")
        return value


class ProductUpdateRequest(BaseModel):
    provider: Optional[str] = None
    name: Optional[str] = None
    cost_price: Optional[float] = None
    cost: Optional[float] = None
    price: Optional[float] = None
    category: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    logo_url: Optional[str] = None
    promo_title: Optional[str] = None
    promo_text: Optional[str] = None
    promo_badge: Optional[str] = None
    promo_url: Optional[str] = None
    active: Optional[int] = Field(default=None, ge=0, le=1)
    display_order: Optional[int] = None


class BulkMarkupRequest(BaseModel):
    brand: str = Field(..., description="Nama brand/provider, atau 'ALL' untuk semua")
    percent: float = Field(..., ge=0, le=500, description="Persentase markup dari modal")
    min_profit: int = Field(..., ge=0, description="Minimal profit dalam Rupiah")


class PromoCreateRequest(BaseModel):
    title: str = Field(..., min_length=1)
    code: Optional[str] = None
    description: Optional[str] = None
    badge: Optional[str] = None
    cta_text: Optional[str] = None
    cta_url: Optional[str] = None
    image_url: Optional[str] = None
    rule_type: str = Field(default="content")
    target_scope: str = Field(default="all")
    target_value: Optional[str] = None
    discount_type: Optional[str] = None
    discount_value: Optional[float] = None
    max_discount: Optional[float] = None
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    show_on_website: int = Field(default=1, ge=0, le=1)
    active: int = Field(default=1, ge=0, le=1)


class PromoUpdateRequest(BaseModel):
    title: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    badge: Optional[str] = None
    cta_text: Optional[str] = None
    cta_url: Optional[str] = None
    image_url: Optional[str] = None
    rule_type: Optional[str] = None
    target_scope: Optional[str] = None
    target_value: Optional[str] = None
    discount_type: Optional[str] = None
    discount_value: Optional[float] = None
    max_discount: Optional[float] = None
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    show_on_website: Optional[int] = Field(default=None, ge=0, le=1)
    active: Optional[int] = Field(default=None, ge=0, le=1)


def _clean_promo_rule_type(value: Optional[str]) -> str:
    cleaned = (_clean_text(value) or "content").lower()
    allowed = {"content", "price"}
    return cleaned if cleaned in allowed else "content"


def _clean_target_scope(value: Optional[str]) -> str:
    cleaned = (_clean_text(value) or "all").lower()
    allowed = {"all", "category", "provider", "sku"}
    return cleaned if cleaned in allowed else "all"


def _clean_discount_type(value: Optional[str]) -> Optional[str]:
    cleaned = (_clean_text(value) or "").lower()
    if not cleaned:
        return None
    allowed = {"percent", "fixed"}
    return cleaned if cleaned in allowed else None


def _clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _clean_optional_url(value: Optional[str]) -> Optional[str]:
    cleaned = _clean_text(value)
    return cleaned or None


def _parse_datetime(value: Optional[str]) -> Optional[str]:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        return parsed.isoformat()
    except ValueError:
        return cleaned


def _normalize_media_path(value: str) -> str:
    return value.replace("\\", "/")


def _build_static_url(relative_path: str) -> str:
    return "/" + _normalize_media_path(relative_path).lstrip("/")


def _product_dict(row: tuple[Any, ...]) -> Dict[str, Any]:
    (
        sku,
        provider,
        name,
        cost_price,
        price,
        active,
        category,
        description,
        image_url,
        logo_url,
        promo_title,
        promo_text,
        promo_badge,
        promo_url,
        display_order,
    ) = row

    cost_value = float(cost_price or 0)
    price_value = float(price or 0)
    normalized_provider = normalized_provider_name(provider, name, category)
    normalized_category = normalized_category_name(category, normalized_provider)

    return {
        "sku": sku,
        "provider": normalized_provider,
        "name": name,
        "cost": cost_value,
        "price": price_value,
        "profit": round(price_value - cost_value, 2),
        "active": bool(active),
        "category": normalized_category,
        "description": description or "",
        "image_url": image_url or "",
        "logo_url": logo_url or "",
        "promo_title": promo_title or "",
        "promo_text": promo_text or "",
        "promo_badge": promo_badge or "",
        "promo_url": promo_url or "",
        "display_order": int(display_order or 0),
    }


def _group_products(rows: list[tuple[Any, ...]]) -> Dict[str, Any]:
    categories: Dict[str, Dict[str, Any]] = {}
    flat_products = []

    for row in rows:
        product = _product_dict(row)
        flat_products.append(product)

        category_name = product["category"]
        provider_name = product["provider"]
        category_entry = categories.setdefault(
            category_name,
            {
                "name": category_name,
                "providers": {},
            },
        )

        provider_entry = category_entry["providers"].setdefault(
            provider_name,
            {
                "name": provider_name,
                "logo_url": product["logo_url"],
                "image_url": product["image_url"],
                "description": product["description"],
                "promo_title": product["promo_title"],
                "promo_text": product["promo_text"],
                "promo_badge": product["promo_badge"],
                "promo_url": product["promo_url"],
                "items": [],
            },
        )

        if not provider_entry["logo_url"] and product["logo_url"]:
            provider_entry["logo_url"] = product["logo_url"]
        if not provider_entry["image_url"] and product["image_url"]:
            provider_entry["image_url"] = product["image_url"]
        if not provider_entry["description"] and product["description"]:
            provider_entry["description"] = product["description"]
        if not provider_entry["promo_title"] and product["promo_title"]:
            provider_entry["promo_title"] = product["promo_title"]
        if not provider_entry["promo_text"] and product["promo_text"]:
            provider_entry["promo_text"] = product["promo_text"]
        if not provider_entry["promo_badge"] and product["promo_badge"]:
            provider_entry["promo_badge"] = product["promo_badge"]
        if not provider_entry["promo_url"] and product["promo_url"]:
            provider_entry["promo_url"] = product["promo_url"]

        provider_entry["items"].append(product)

    grouped_categories = []
    for category_name in sorted(categories.keys()):
        provider_map = categories[category_name]["providers"]
        grouped_categories.append(
            {
                "name": category_name,
                "providers": [provider_map[key] for key in sorted(provider_map.keys())],
            }
        )

    return {"categories": grouped_categories, "products": flat_products}


async def _ensure_default_admin() -> None:
    username = settings.default_admin_username or "admin"
    password = settings.default_admin_password

    if not password:
        return

    existing = await db_query("SELECT id FROM admin WHERE username=:username", {"username": username})
    if existing:
        return

    await db_execute(
        "INSERT INTO admin (username, password) VALUES (:username, :password)",
        {"username": username, "password": hash_password(password)},
    )


async def _require_admin(token: Optional[str] = Header(None, alias="token")) -> Dict[str, Any]:
    if token is None:
        raise HTTPException(status_code=401, detail="Token admin diperlukan")

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token admin tidak valid")

    return payload


@router.get("/health")
async def admin_health() -> Dict[str, Any]:
    return {"status": "ok", "service": "lixafa-admin"}


@router.post("/login", response_model=AdminLoginResponse)
async def admin_login(payload: AdminLoginRequest) -> Dict[str, Any]:
    await _ensure_default_admin()
    row = await db_query("SELECT password FROM admin WHERE username=:username", {"username": payload.username})
    if not row:
        raise HTTPException(status_code=401, detail="Kredensial tidak valid")

    if not verify_password(payload.password, row[0][0]):
        raise HTTPException(status_code=401, detail="Kredensial tidak valid")

    token = create_access_token({"sub": payload.username, "role": "admin"})
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in_minutes": settings.jwt_access_token_expire_minutes,
        "username": payload.username,
        "token": token,
        "message": "Login berhasil",
    }


@router.get("/api/products")
async def get_admin_products(_: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
    rows = await db_query(
        """
        SELECT sku, provider, name, cost_price, price, active, category,
               description, image_url, logo_url, promo_title, promo_text, promo_badge, promo_url, display_order
        FROM products
        ORDER BY COALESCE(category, ''), COALESCE(provider, ''), COALESCE(display_order, 0), name
        """
    )
    return _group_products(rows)


@router.post("/api/products")
async def create_product(payload: ProductCreateRequest, _: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
    provider = _clean_text(payload.provider) or "Lainnya"
    name = _clean_text(payload.name) or "Produk Baru"
    sku = _clean_text(payload.sku) or f"{provider}-{name}".replace(" ", "-").lower()
    cost_price = payload.cost_price if payload.cost_price is not None else payload.cost
    category = _clean_text(payload.category) or "Lainnya"

    await db_execute(
        """
        INSERT INTO products (
            sku, provider, name, cost_price, price, active, category,
            description, image_url, logo_url, promo_title, promo_text, promo_badge, promo_url, display_order
        )
        VALUES (
            :sku, :provider, :name, :cost_price, :price, :active, :category,
            :description, :image_url, :logo_url, :promo_title, :promo_text, :promo_badge, :promo_url, :display_order
        )
        ON CONFLICT(sku) DO UPDATE SET
            provider = EXCLUDED.provider,
            name = EXCLUDED.name,
            cost_price = EXCLUDED.cost_price,
            price = EXCLUDED.price,
            active = EXCLUDED.active,
            category = EXCLUDED.category,
            description = EXCLUDED.description,
            image_url = EXCLUDED.image_url,
            logo_url = EXCLUDED.logo_url,
            promo_title = EXCLUDED.promo_title,
            promo_text = EXCLUDED.promo_text,
            promo_badge = EXCLUDED.promo_badge,
            promo_url = EXCLUDED.promo_url,
            display_order = EXCLUDED.display_order
        """,
        {
            "sku": sku,
            "provider": provider,
            "name": name,
            "cost_price": cost_price,
            "price": payload.price,
            "active": payload.active,
            "category": category,
            "description": _clean_text(payload.description),
            "image_url": _clean_optional_url(payload.image_url),
            "logo_url": _clean_optional_url(payload.logo_url),
            "promo_title": _clean_text(payload.promo_title),
            "promo_text": _clean_text(payload.promo_text),
            "promo_badge": _clean_text(payload.promo_badge),
            "promo_url": _clean_optional_url(payload.promo_url),
            "display_order": payload.display_order,
        },
    )
    return {"success": True, "message": "Produk berhasil disimpan", "sku": sku}


@router.put("/api/products/{sku}")
async def update_product(sku: str, payload: ProductUpdateRequest, _: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
    updates: list[str] = []
    values: Dict[str, Any] = {"sku": sku}

    provider = _clean_text(payload.provider)
    name = _clean_text(payload.name)
    category = _clean_text(payload.category)
    description = _clean_text(payload.description)
    image_url = _clean_optional_url(payload.image_url)
    logo_url = _clean_optional_url(payload.logo_url)
    promo_title = _clean_text(payload.promo_title)
    promo_text = _clean_text(payload.promo_text)
    promo_badge = _clean_text(payload.promo_badge)
    promo_url = _clean_optional_url(payload.promo_url)
    cost_price = payload.cost_price if payload.cost_price is not None else payload.cost

    if provider is not None:
        updates.append("provider=:provider")
        values["provider"] = provider
    if name is not None:
        updates.append("name=:name")
        values["name"] = name
    if cost_price is not None:
        updates.append("cost_price=:cost_price")
        values["cost_price"] = cost_price
    if payload.price is not None:
        updates.append("price=:price")
        values["price"] = payload.price
    if category is not None:
        updates.append("category=:category")
        values["category"] = category
    if description is not None:
        updates.append("description=:description")
        values["description"] = description
    if image_url is not None:
        updates.append("image_url=:image_url")
        values["image_url"] = image_url
    if logo_url is not None:
        updates.append("logo_url=:logo_url")
        values["logo_url"] = logo_url
    if promo_title is not None:
        updates.append("promo_title=:promo_title")
        values["promo_title"] = promo_title
    if promo_text is not None:
        updates.append("promo_text=:promo_text")
        values["promo_text"] = promo_text
    if promo_badge is not None:
        updates.append("promo_badge=:promo_badge")
        values["promo_badge"] = promo_badge
    if promo_url is not None:
        updates.append("promo_url=:promo_url")
        values["promo_url"] = promo_url
    if payload.active is not None:
        updates.append("active=:active")
        values["active"] = payload.active
    if payload.display_order is not None:
        updates.append("display_order=:display_order")
        values["display_order"] = payload.display_order

    if not updates:
        raise HTTPException(status_code=400, detail="Tidak ada data yang diperbarui")

    await db_execute(f"UPDATE products SET {', '.join(updates)} WHERE sku=:sku", values)
    return {"success": True, "message": "Produk berhasil diperbarui"}


@router.put("/api/products/{sku}/toggle")
async def toggle_product(sku: str, _: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
    row = await db_query("SELECT active FROM products WHERE sku=:sku", {"sku": sku})
    if not row:
        raise HTTPException(status_code=404, detail="Produk tidak ditemukan")

    new_active = 0 if bool(row[0][0]) else 1
    await db_execute("UPDATE products SET active=:active WHERE sku=:sku", {"active": new_active, "sku": sku})
    return {"success": True, "active": bool(new_active)}


@router.delete("/api/products/{sku}")
async def delete_product(sku: str, _: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
    await db_execute("DELETE FROM products WHERE sku=:sku", {"sku": sku})
    return {"success": True, "message": "Produk berhasil dihapus"}


@router.get("/api/orders")
async def get_orders(_: Dict[str, Any] = Depends(_require_admin)) -> list[Dict[str, Any]]:
    rows = await db_query(
        "SELECT id, phone, target_id, nickname, nominal, amount, payment_status, topup_status, sn, note, created_at FROM topup ORDER BY created_at DESC LIMIT 100"
    )

    return [
        {
            "id": row[0],
            "phone": row[1],
            "target_id": row[2],
            "nickname": row[3],
            "nominal": row[4],
            "amount": float(row[5] or 0),
            "payment_status": row[6],
            "topup_status": row[7],
            "sn": row[8],
            "note": row[9],
            "created_at": row[10].isoformat() if hasattr(row[10], "isoformat") else str(row[10]),
        }
        for row in rows
    ]


@router.get("/api/revenue-today")
async def revenue_today(_: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
    rows = await db_query("SELECT amount, payment_status, created_at FROM topup")
    today = datetime.now(timezone.utc).date()
    total = 0.0
    count = 0
    for amount, payment_status, created_at in rows:
        if payment_status != "PAID":
            continue
        if not created_at:
            continue
        try:
            created_date = created_at.date() if hasattr(created_at, "date") else datetime.fromisoformat(str(created_at)).date()
        except Exception:
            continue
        if created_date == today:
            total += float(amount or 0)
            count += 1
    return {"revenue": round(total, 2), "count": count, "period": "today"}


@router.get("/api/revenue-total")
async def revenue_total(_: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
    rows = await db_query("SELECT amount, payment_status FROM topup")
    total = sum(float(amount or 0) for amount, payment_status in rows if payment_status == "PAID")
    return {"revenue": round(total, 2), "count": len([1 for _, payment_status in rows if payment_status == "PAID"]), "period": "all"}


@router.get("/api/profit-today")
async def profit_today(_: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
    revenue = await revenue_today(_)
    return {"profit": round(revenue["revenue"] * 0.1, 2), "period": "today"}


@router.get("/api/profit-total")
async def profit_total(_: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
    revenue = await revenue_total(_)
    return {"profit": round(revenue["revenue"] * 0.1, 2), "period": "all"}


@router.post("/bulk-markup")
async def bulk_markup(payload: BulkMarkupRequest, _: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
    brand = payload.brand.upper()
    percent = payload.percent
    min_profit = payload.min_profit

    if brand == "ALL":
        rows = await db_query("SELECT sku, cost_price, price FROM products")
    else:
        rows = await db_query(
            "SELECT sku, cost_price, price FROM products WHERE UPPER(provider)=:brand",
            {"brand": brand},
        )

    updated = 0
    for sku, cost_price, current_price in rows:
        cost = float(cost_price or 0)
        if cost <= 0:
            continue
        new_price = max(float(current_price or cost), cost + max(min_profit, int(cost * percent / 100)))
        await db_execute("UPDATE products SET price=:price WHERE sku=:sku", {"price": new_price, "sku": sku})
        updated += 1

    return {"success": True, "message": f"Harga berhasil diperbarui untuk {updated} produk"}


@router.post("/sync-products")
async def sync_products(_: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
    provider_products = await get_digiflazz_products()
    if isinstance(provider_products, str):
        raise HTTPException(status_code=400, detail=provider_products)

    if not isinstance(provider_products, list):
        raise HTTPException(status_code=400, detail="Format data provider tidak valid")

    inserted = 0
    for item in provider_products:
        if not isinstance(item, dict):
            continue

        sku = item.get("sku") or item.get("buyer_sku_code") or item.get("product_code")
        if not sku:
            continue

        provider = item.get("brand") or item.get("provider") or item.get("seller_name") or "DIGIFLAZZ"
        name = item.get("product_name") or item.get("name") or sku
        price = item.get("price") or item.get("sell_price") or item.get("price_sales")
        cost_price = item.get("cost_price") or item.get("modal") or price
        category = normalized_category_name(item.get("category"), provider)
        provider = normalized_provider_name(provider, name, category)
        description = item.get("desc") or item.get("description") or item.get("remarks") or ""

        await db_execute(
            """
            INSERT INTO products (
                sku, provider, name, cost_price, price, active, category,
                description, image_url, logo_url, promo_title, promo_text, promo_badge, promo_url, display_order
            )
            VALUES (
                :sku, :provider, :name, :cost_price, :price, :active, :category,
                :description, :image_url, :logo_url, :promo_title, :promo_text, :promo_badge, :promo_url, :display_order
            )
            ON CONFLICT(sku) DO UPDATE SET
                provider = EXCLUDED.provider,
                name = EXCLUDED.name,
                cost_price = EXCLUDED.cost_price,
                price = EXCLUDED.price,
                active = EXCLUDED.active,
                category = EXCLUDED.category,
                description = COALESCE(NULLIF(EXCLUDED.description, ''), products.description),
                image_url = COALESCE(NULLIF(EXCLUDED.image_url, ''), products.image_url),
                logo_url = COALESCE(NULLIF(EXCLUDED.logo_url, ''), products.logo_url),
                promo_title = COALESCE(NULLIF(EXCLUDED.promo_title, ''), products.promo_title),
                promo_text = COALESCE(NULLIF(EXCLUDED.promo_text, ''), products.promo_text),
                promo_badge = COALESCE(NULLIF(EXCLUDED.promo_badge, ''), products.promo_badge),
                promo_url = COALESCE(NULLIF(EXCLUDED.promo_url, ''), products.promo_url),
                display_order = EXCLUDED.display_order
            """,
            {
                "sku": sku,
                "provider": provider,
                "name": name,
                "cost_price": cost_price,
                "price": price,
                "active": 1,
                "category": category,
                "description": description,
                "image_url": item.get("image_url") or item.get("product_image") or "",
                "logo_url": item.get("logo_url") or item.get("provider_logo") or "",
                "promo_title": item.get("promo_title") or "",
                "promo_text": item.get("promo_text") or "",
                "promo_badge": item.get("promo_badge") or "",
                "promo_url": item.get("promo_url") or "",
                "display_order": item.get("display_order") or 0,
            },
        )
        inserted += 1

    return {"success": True, "message": f"Sinkronisasi selesai: {inserted} produk diproses"}


@router.post("/upload-image")
async def upload_image(
    file: UploadFile = File(...),
    folder: str = Form(default="products"),
    _: Dict[str, Any] = Depends(_require_admin),
) -> Dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="File tidak valid")

    allowed_folders = {"products", "promos", "logos", "brand"}
    folder_name = folder.strip().lower() if folder else "products"
    if folder_name not in allowed_folders:
        folder_name = "products"

    suffix = Path(file.filename).suffix.lower()
    filename = f"{uuid4().hex}{suffix}"
    upload_dir = Path("web") / "uploads" / "admin" / folder_name
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_path = upload_dir / filename
    file_path.write_bytes(await file.read())

    return {"success": True, "url": _build_static_url(str(file_path.as_posix())), "filename": filename}


def _promo_dict(row: tuple[Any, ...]) -> Dict[str, Any]:
    (
        promo_id,
        title,
        code,
        description,
        badge,
        cta_text,
        cta_url,
        image_url,
        rule_type,
        target_scope,
        target_value,
        discount_type,
        discount_value,
        max_discount,
        starts_at,
        ends_at,
        show_on_website,
        active,
        created_at,
        updated_at,
    ) = row

    return {
        "id": promo_id,
        "title": title,
        "code": code or "",
        "description": description or "",
        "badge": badge or "",
        "cta_text": cta_text or "",
        "cta_url": cta_url or "",
        "image_url": image_url or "",
        "rule_type": rule_type or "content",
        "target_scope": target_scope or "all",
        "target_value": target_value or "",
        "discount_type": discount_type or "",
        "discount_value": float(discount_value or 0),
        "max_discount": float(max_discount or 0),
        "starts_at": starts_at.isoformat() if hasattr(starts_at, "isoformat") else starts_at,
        "ends_at": ends_at.isoformat() if hasattr(ends_at, "isoformat") else ends_at,
        "show_on_website": bool(show_on_website),
        "active": bool(active),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
    }


@router.get("/api/promos")
async def get_promos(_: Dict[str, Any] = Depends(_require_admin)) -> list[Dict[str, Any]]:
    rows = await db_query(
        "SELECT id, title, code, description, badge, cta_text, cta_url, image_url, rule_type, target_scope, target_value, discount_type, discount_value, max_discount, starts_at, ends_at, show_on_website, active, created_at, updated_at FROM promos ORDER BY active DESC, created_at DESC"
    )
    return [_promo_dict(row) for row in rows]


@router.post("/api/promos")
async def create_promo(payload: PromoCreateRequest, _: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
    rule_type = _clean_promo_rule_type(payload.rule_type)
    code = _clean_text(payload.code)
    target_scope = _clean_target_scope(payload.target_scope)
    discount_type = _clean_discount_type(payload.discount_type)
    discount_value = payload.discount_value
    max_discount = payload.max_discount

    if code and rule_type != "price":
        raise HTTPException(status_code=400, detail="Promo berkode harus memakai rule_type=price")

    if rule_type == "price":
        if discount_type not in {"percent", "fixed"}:
            raise HTTPException(status_code=400, detail="discount_type wajib percent/fixed untuk promo harga")
        if discount_value is None or float(discount_value) <= 0:
            raise HTTPException(status_code=400, detail="discount_value wajib > 0 untuk promo harga")
    else:
        discount_type = None
        discount_value = None
        max_discount = None

    await db_execute(
        """
        INSERT INTO promos (
            title, code, description, badge, cta_text, cta_url, image_url,
            rule_type, target_scope, target_value, discount_type, discount_value, max_discount,
            starts_at, ends_at, show_on_website, active
        )
        VALUES (
            :title, :code, :description, :badge, :cta_text, :cta_url, :image_url,
            :rule_type, :target_scope, :target_value, :discount_type, :discount_value, :max_discount,
            :starts_at, :ends_at, :show_on_website, :active
        )
        """,
        {
            "title": _clean_text(payload.title),
            "code": code,
            "description": _clean_text(payload.description),
            "badge": _clean_text(payload.badge),
            "cta_text": _clean_text(payload.cta_text),
            "cta_url": _clean_optional_url(payload.cta_url),
            "image_url": _clean_optional_url(payload.image_url),
            "rule_type": rule_type,
            "target_scope": target_scope,
            "target_value": _clean_text(payload.target_value),
            "discount_type": discount_type,
            "discount_value": discount_value,
            "max_discount": max_discount,
            "starts_at": _parse_datetime(payload.starts_at),
            "ends_at": _parse_datetime(payload.ends_at),
            "show_on_website": payload.show_on_website,
            "active": payload.active,
        },
    )
    return {"success": True, "message": "Promo berhasil disimpan"}


@router.put("/api/promos/{promo_id}")
async def update_promo(promo_id: int, payload: PromoUpdateRequest, _: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
    existing = await db_query(
        "SELECT rule_type, code, discount_type, discount_value FROM promos WHERE id=:promo_id",
        {"promo_id": promo_id},
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Promo tidak ditemukan")

    existing_rule_type, existing_code, existing_discount_type, existing_discount_value = existing[0]

    updates: list[str] = []
    values: Dict[str, Any] = {"promo_id": promo_id}

    if payload.title is not None:
        updates.append("title=:title")
        values["title"] = _clean_text(payload.title)
    if payload.code is not None:
        updates.append("code=:code")
        values["code"] = _clean_text(payload.code)
    if payload.description is not None:
        updates.append("description=:description")
        values["description"] = _clean_text(payload.description)
    if payload.badge is not None:
        updates.append("badge=:badge")
        values["badge"] = _clean_text(payload.badge)
    if payload.cta_text is not None:
        updates.append("cta_text=:cta_text")
        values["cta_text"] = _clean_text(payload.cta_text)
    if payload.cta_url is not None:
        updates.append("cta_url=:cta_url")
        values["cta_url"] = _clean_optional_url(payload.cta_url)
    if payload.image_url is not None:
        updates.append("image_url=:image_url")
        values["image_url"] = _clean_optional_url(payload.image_url)
    if payload.rule_type is not None:
        updates.append("rule_type=:rule_type")
        values["rule_type"] = _clean_promo_rule_type(payload.rule_type)
    if payload.target_scope is not None:
        updates.append("target_scope=:target_scope")
        values["target_scope"] = _clean_target_scope(payload.target_scope)
    if payload.target_value is not None:
        updates.append("target_value=:target_value")
        values["target_value"] = _clean_text(payload.target_value)
    if payload.discount_type is not None:
        updates.append("discount_type=:discount_type")
        values["discount_type"] = _clean_discount_type(payload.discount_type)
    if payload.discount_value is not None:
        updates.append("discount_value=:discount_value")
        values["discount_value"] = payload.discount_value
    if payload.max_discount is not None:
        updates.append("max_discount=:max_discount")
        values["max_discount"] = payload.max_discount
    if payload.starts_at is not None:
        updates.append("starts_at=:starts_at")
        values["starts_at"] = _parse_datetime(payload.starts_at)
    if payload.ends_at is not None:
        updates.append("ends_at=:ends_at")
        values["ends_at"] = _parse_datetime(payload.ends_at)
    if payload.show_on_website is not None:
        updates.append("show_on_website=:show_on_website")
        values["show_on_website"] = payload.show_on_website
    if payload.active is not None:
        updates.append("active=:active")
        values["active"] = payload.active

    effective_rule_type = values.get("rule_type", _clean_promo_rule_type(existing_rule_type))
    effective_code = values.get("code", _clean_text(existing_code))
    effective_discount_type = values.get("discount_type", _clean_discount_type(existing_discount_type))
    effective_discount_value = values.get("discount_value", existing_discount_value)

    if effective_code and effective_rule_type != "price":
        raise HTTPException(status_code=400, detail="Promo berkode harus memakai rule_type=price")

    if effective_rule_type == "price":
        if effective_discount_type not in {"percent", "fixed"}:
            raise HTTPException(status_code=400, detail="discount_type wajib percent/fixed untuk promo harga")
        if effective_discount_value is None or float(effective_discount_value) <= 0:
            raise HTTPException(status_code=400, detail="discount_value wajib > 0 untuk promo harga")
    else:
        values["discount_type"] = None
        values["discount_value"] = None
        values["max_discount"] = None
        if "discount_type=:discount_type" not in updates:
            updates.append("discount_type=:discount_type")
        if "discount_value=:discount_value" not in updates:
            updates.append("discount_value=:discount_value")
        if "max_discount=:max_discount" not in updates:
            updates.append("max_discount=:max_discount")

    if not updates:
        raise HTTPException(status_code=400, detail="Tidak ada data yang diperbarui")

    await db_execute(f"UPDATE promos SET {', '.join(updates)}, updated_at=CURRENT_TIMESTAMP WHERE id=:promo_id", values)
    return {"success": True, "message": "Promo berhasil diperbarui"}


@router.delete("/api/promos/{promo_id}")
async def delete_promo(promo_id: int, _: Dict[str, Any] = Depends(_require_admin)) -> Dict[str, Any]:
    await db_execute("DELETE FROM promos WHERE id=:promo_id", {"promo_id": promo_id})
    return {"success": True, "message": "Promo berhasil dihapus"}