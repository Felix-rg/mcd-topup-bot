from typing import Any, Dict

from app.core.write_quiescence import write_quiescence
from app.database import db_execute
from app.services.digiflazz_service import get_digiflazz_products


def _truthy_provider_flag(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "aktif", "active"}


async def sync_digiflazz_products() -> Dict[str, Any]:
    # Guard before the first provider call so direct admin/script invocation
    # cannot reach Digiflazz or the database during the maintenance window.
    async with write_quiescence.writer_section("catalog_sync"):
        return await _sync_digiflazz_products()


async def _sync_digiflazz_products() -> Dict[str, Any]:
    provider_batches: list[tuple[str, Any]] = [
        ("prepaid", await get_digiflazz_products(cmd="prepaid")),
        ("postpaid", await get_digiflazz_products(cmd="pasca")),
    ]

    processed = 0
    errors: list[str] = []
    for product_type, provider_products in provider_batches:
        if isinstance(provider_products, str):
            errors.append(f"{product_type}: {provider_products}")
            continue
        if not isinstance(provider_products, list):
            errors.append(f"{product_type}: Format data provider tidak valid")
            continue

        for item in provider_products:
            if not isinstance(item, dict):
                continue
            sku = item.get("sku") or item.get("buyer_sku_code") or item.get("product_code")
            if not sku:
                continue

            provider = item.get("brand") or item.get("provider") or item.get("seller_name") or "DIGIFLAZZ"
            name = item.get("product_name") or item.get("name") or sku
            provider_price = item.get("price") or item.get("sell_price") or item.get("price_sales") or 0
            cost_price = item.get("cost_price") or provider_price or 0
            category = item.get("category") or ("Pascabayar" if product_type == "postpaid" else "Lainnya")
            buyer_status = 1 if _truthy_provider_flag(item.get("buyer_product_status"), default=True) else 0
            seller_status = 1 if _truthy_provider_flag(item.get("seller_product_status"), default=True) else 0
            active = 1 if buyer_status and seller_status else 0

            await db_execute(
                """
                INSERT INTO products (
                    sku, provider, name, cost_price, price, active, category,
                    product_type, brand, provider_type, buyer_product_status,
                    seller_product_status, stock, unlimited_stock, multi,
                    start_cut_off, end_cut_off, admin_fee, commission,
                    provider_description
                )
                VALUES (
                    :sku, :provider, :name, :cost_price, :price, :active, :category,
                    :product_type, :brand, :provider_type, :buyer_product_status,
                    :seller_product_status, :stock, :unlimited_stock, :multi,
                    :start_cut_off, :end_cut_off, :admin_fee, :commission,
                    :provider_description
                )
                ON CONFLICT(sku) DO UPDATE SET
                    provider = EXCLUDED.provider,
                    name = EXCLUDED.name,
                    cost_price = EXCLUDED.cost_price,
                    price = CASE
                        WHEN products.price IS NULL OR products.price <= products.cost_price THEN EXCLUDED.price
                        ELSE products.price
                    END,
                    active = EXCLUDED.active,
                    category = EXCLUDED.category,
                    product_type = EXCLUDED.product_type,
                    brand = EXCLUDED.brand,
                    provider_type = EXCLUDED.provider_type,
                    buyer_product_status = EXCLUDED.buyer_product_status,
                    seller_product_status = EXCLUDED.seller_product_status,
                    stock = EXCLUDED.stock,
                    unlimited_stock = EXCLUDED.unlimited_stock,
                    multi = EXCLUDED.multi,
                    start_cut_off = EXCLUDED.start_cut_off,
                    end_cut_off = EXCLUDED.end_cut_off,
                    admin_fee = EXCLUDED.admin_fee,
                    commission = EXCLUDED.commission,
                    provider_description = EXCLUDED.provider_description
                """,
                {
                    "sku": sku,
                    "provider": provider,
                    "name": name,
                    "cost_price": cost_price,
                    "price": provider_price,
                    "active": active,
                    "category": category,
                    "product_type": product_type,
                    "brand": item.get("brand") or provider,
                    "provider_type": item.get("type") or item.get("product_type") or "",
                    "buyer_product_status": buyer_status,
                    "seller_product_status": seller_status,
                    "stock": item.get("stock"),
                    "unlimited_stock": 1 if _truthy_provider_flag(item.get("unlimited_stock"), default=False) else 0,
                    "multi": 1 if _truthy_provider_flag(item.get("multi"), default=False) else 0,
                    "start_cut_off": item.get("start_cut_off") or "",
                    "end_cut_off": item.get("end_cut_off") or "",
                    "admin_fee": item.get("admin") or item.get("admin_fee") or 0,
                    "commission": item.get("commission") or 0,
                    "provider_description": item.get("desc") or item.get("description") or "",
                },
            )
            processed += 1

    return {
        "success": not errors,
        "processed": processed,
        "errors": errors,
        "message": f"Sinkronisasi selesai: {processed} produk diproses"
        + (f" ({'; '.join(errors)})" if errors else ""),
    }
