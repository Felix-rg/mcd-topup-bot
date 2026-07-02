import hashlib
import json
import hmac
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from app.config import DIGIFLAZZ_SECRET, TRIPAY_PRIVATE_KEY
from app.core.settings import settings
from app.database import db_execute, db_query
from app.services.digiflazz_service import kirim_digiflazz
from app.services.nickname_service import check_game_nickname
from app.services.order_service import calculate_total_amount, create_order_id
from app.services.product_utils import normalized_category_name, normalized_provider_name
from app.services.tripay_service import create_invoice

router = APIRouter()
LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc


def _normalize_match_token(value: Any, *, harmonize_game_terms: bool = True) -> str:
    raw = str(value or "").strip().lower()
    # Samakan ejaan populer agar target promo provider/category lebih toleran.
    if harmonize_game_terms:
        raw = raw.replace("legends", "legend")
    return "".join(ch for ch in raw if ch.isalnum())


def _split_target_values(value: str) -> list[str]:
    if not value:
        return []
    parts: list[str] = []
    for raw in value.replace("\n", ",").split(","):
        cleaned = raw.strip()
        if cleaned:
            parts.append(cleaned)
    return parts


def _matches_target(target_value: str, actual_value: str, *, exact: bool = False) -> bool:
    actual_token = _normalize_match_token(actual_value, harmonize_game_terms=not exact)
    if not actual_token:
        return False

    targets = _split_target_values(target_value)
    if not targets:
        return False

    for item in targets:
        target_token = _normalize_match_token(item, harmonize_game_terms=not exact)
        if not target_token:
            continue
        if exact:
            if target_token == actual_token:
                return True
            continue
        if target_token == actual_token or target_token in actual_token or actual_token in target_token:
            return True
    return False


def _to_utc_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo:
            return value.astimezone(timezone.utc)
        return value.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        if parsed.tzinfo:
            return parsed.astimezone(timezone.utc)
        return parsed.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    except ValueError:
        return None


def _promo_matches(promo: Dict[str, Any], product: Dict[str, Any], now_utc: datetime) -> bool:
    if not promo.get("active"):
        return False

    starts_at = _to_utc_datetime(promo.get("starts_at"))
    ends_at = _to_utc_datetime(promo.get("ends_at"))

    if starts_at and now_utc < starts_at:
        return False
    if ends_at and now_utc > ends_at:
        return False

    scope = (promo.get("target_scope") or "all").lower()
    target_value = (promo.get("target_value") or "").strip()

    if scope == "all":
        return True
    if scope == "sku":
        # SKU wajib cocok persis agar promo tidak bocor ke SKU lain.
        return _matches_target(target_value, str(product.get("sku") or ""), exact=True)
    if scope == "provider":
        return _matches_target(target_value, str(product.get("provider") or ""))
    if scope == "category":
        return _matches_target(target_value, str(product.get("category") or ""))
    return False


def _apply_price_promo(base_price: float, promo: Dict[str, Any]) -> float:
    discount_type = (promo.get("discount_type") or "").lower()
    discount_value = float(promo.get("discount_value") or 0)
    max_discount = float(promo.get("max_discount") or 0)

    if base_price <= 0 or discount_value <= 0:
        return base_price

    if discount_type == "percent":
        discount = base_price * (discount_value / 100)
    elif discount_type == "fixed":
        discount = discount_value
    else:
        return base_price

    if max_discount > 0:
        discount = min(discount, max_discount)

    return max(base_price - discount, 0)


async def _get_active_price_promos() -> list[Dict[str, Any]]:
    rows = await db_query(
        """
        SELECT id, title, code, badge, rule_type, target_scope, target_value,
               discount_type, discount_value, max_discount, starts_at, ends_at, active
        FROM promos
        WHERE active=1
        ORDER BY created_at DESC
        """
    )

    promos: list[Dict[str, Any]] = []
    for row in rows:
        promos.append(
            {
                "id": row[0],
                "title": row[1] or "",
                "code": row[2] or "",
                "badge": row[3] or "",
                "rule_type": row[4] or "content",
                "target_scope": row[5] or "all",
                "target_value": row[6] or "",
                "discount_type": row[7] or "",
                "discount_value": float(row[8] or 0),
                "max_discount": float(row[9] or 0),
                "starts_at": row[10],
                "ends_at": row[11],
                "active": bool(row[12]),
            }
        )
    return promos


def _apply_best_price_promo(
    product: Dict[str, Any],
    promos: list[Dict[str, Any]],
    now_utc: datetime,
    promo_code: Optional[str] = None,
) -> Dict[str, Any]:
    base_price = float(product.get("price") or 0)
    best_price = base_price
    best_promo: Optional[Dict[str, Any]] = None
    normalized_code = (promo_code or "").strip().lower()
    has_input_code = bool(normalized_code)

    for promo in promos:
        if (promo.get("rule_type") or "").lower() != "price":
            continue
        if not _promo_matches(promo, product, now_utc):
            continue

        required_code = str(promo.get("code") or "").strip().lower()

        # Jika user memasukkan kode promo, hanya promo berkode yang boleh diproses.
        if has_input_code:
            if not required_code:
                continue
            if required_code != normalized_code:
                continue
        else:
            # Tanpa input kode: abaikan promo yang memang butuh kode.
            if required_code:
                continue

        candidate_price = _apply_price_promo(base_price, promo)
        if candidate_price < best_price:
            best_price = candidate_price
            best_promo = promo

    if best_promo and best_price < base_price:
        product["original_price"] = base_price
        product["price"] = round(best_price, 2)
        product["promo_applied"] = {
            "id": best_promo.get("id"),
            "title": best_promo.get("title"),
            "badge": best_promo.get("badge"),
            "code": best_promo.get("code") or "",
            "discount_type": best_promo.get("discount_type"),
            "discount_value": best_promo.get("discount_value"),
        }
    else:
        product["original_price"] = base_price

    return product


async def _resolve_effective_price_by_sku(sku: str, promo_code: Optional[str] = None) -> float:
    row = await db_query(
        """
        SELECT sku, provider, name, price, category
        FROM products
        WHERE sku=:sku
        """,
        {"sku": sku},
    )
    if not row:
        raise HTTPException(400, "Produk tidak ditemukan")

    sku_value, provider, name, base_price, category = row[0]
    product = {
        "sku": sku_value,
        "provider": normalized_provider_name(provider, name, category),
        "name": name,
        "price": float(base_price or 0),
        "category": normalized_category_name(category, normalized_provider_name(provider, name, category)),
    }

    promos = await _get_active_price_promos()
    now_utc = datetime.now(timezone.utc)
    product = _apply_best_price_promo(product, promos, now_utc, promo_code=promo_code)
    return float(product.get("price") or 0)


def resolve_public_base_url(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")

    if forwarded_host:
        scheme = (forwarded_proto or request.url.scheme or "https").strip()
        return f"{scheme}://{forwarded_host}".rstrip("/")

    return settings.app_base_url.rstrip("/")


class TopupRequest(BaseModel):
    phone: Optional[str] = Field(default=None)
    target_id: Optional[str] = Field(default=None)
    nominal: Optional[str] = Field(default=None)
    method: Optional[str] = Field(default=None)
    promo_code: Optional[str] = Field(default=None)
    nickname: str = Field(default="-")


@router.post("/topup")
async def topup(data: TopupRequest, request: Request) -> Dict[str, Any]:
    wa_pembeli = data.phone
    target_id = data.target_id
    sku = data.nominal
    method = data.method
    promo_code = (data.promo_code or "").strip() or None
    nickname = data.nickname or "-"

    if not wa_pembeli or not target_id or not sku or not method:
        raise HTTPException(status_code=400, detail="phone, target_id, nominal, dan method wajib diisi")

    # Gunakan harga efektif setelah aturan promo aktif diterapkan.
    price_val = await _resolve_effective_price_by_sku(sku, promo_code=promo_code)

    total_bayar = calculate_total_amount(price_val, method)
    order_id = create_order_id()

    try:
        # POSTGRESQL SYNTAX (DICTIONARY PARAMETER)
        await db_execute(
            """INSERT INTO topup (id, phone, target_id, nickname, nominal, amount, payment_status) 
               VALUES (:id, :phone, :target_id, :nickname, :nominal, :amount, 'UNPAID')""",
            {
                "id": order_id, "phone": wa_pembeli, "target_id": target_id, 
                "nickname": nickname, "nominal": sku, "amount": total_bayar
            }
        )
    except Exception as e:
        print(f"DATABASE ERROR: {e}")
        raise HTTPException(500, f"Gagal simpan database: {str(e)}")

    try:
        # WAJIB AWAIT
        public_base_url = resolve_public_base_url(request)
        tripay_res = await create_invoice(
            order_id=order_id,
            amount=total_bayar,
            method=method,
            customer_name="Customer LIXAFA",
            customer_email="customer@lixafa.id",
            customer_phone=wa_pembeli,
            callback_url=f"{public_base_url}/callback",
            return_url=f"{public_base_url}/",
        )
        
        if not tripay_res or not tripay_res.get("checkout_url"):
            error_detail = tripay_res.get("error") or "Tripay tidak mengembalikan checkout_url"
            raise Exception(f"Gagal mendapatkan link pembayaran dari Tripay: {error_detail}")

        invoice_url = tripay_res.get("checkout_url")
        qr_url = tripay_res.get("qr_url")

        # POSTGRESQL SYNTAX
        await db_execute(
            "UPDATE topup SET invoice_url=:url WHERE id=:id", 
            {"url": invoice_url, "id": order_id}
        )
        
        return {"id": order_id, "invoice_url": invoice_url, "qr_url": qr_url or ""}
    except Exception as e:
        print(f"TRIPAY ERROR: {e}")
        raise HTTPException(500, f"Error Tripay: {str(e)}")

@router.get("/topup/{identifier}")
async def cek_status_pesanan(identifier: str):
    """
    Mencari pesanan berdasarkan Order ID (id) atau Nomor HP (phone)
    """
    rows = await db_query(
        """
    SELECT id, 
           topup_status AS status, 
           invoice_url, 
           target_id, 
           nominal AS nominal_name,
           phone,
           payment_status,
           sn,
           note
    FROM topup 
    WHERE id = :identifier OR phone = :identifier
    ORDER BY created_at DESC 
    LIMIT 1
""",
        {"identifier": identifier},
    )

    if not rows:
        raise HTTPException(status_code=404, detail="Pesanan tidak ditemukan. Pastikan Nomor HP atau Order ID benar.")

    row = rows[0]

    raw_status = (row[1] or "").upper()
    payment_status = (row[6] or "").upper()

    if raw_status == "SUCCESS":
        stage = "success"
    elif raw_status == "FAILED":
        stage = "failed"
    elif raw_status == "PENDING_PROVIDER":
        stage = "provider_pending"
    elif payment_status == "PAID" or raw_status in {"PROCESSING", "PAID"}:
        stage = "processing"
    else:
        stage = "pending_payment"

    return {
        "id": row[0],
        "status": row[1],
        "stage": stage,
        "invoice_url": row[2],
        "target_id": row[3],
        "nominal_name": row[4],
        "phone": row[5],
        "payment_status": row[6],
        "sn": row[7],
        "note": row[8],
    }

# Buat schema model untuk request checker
class NicknameRequest(BaseModel):
    game_code: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    zone_id: str = Field(default="")

# Endpoint API baru
@router.post("/check-nickname")
async def api_check_nickname(req: NicknameRequest):
    nickname = await check_game_nickname(req.game_code, req.user_id, req.zone_id)
    
    if not nickname:
        # Mengembalikan error 400 jika ID tidak valid
        raise HTTPException(status_code=400, detail="ID Game tidak ditemukan atau salah ketik.")
    
    return {"status": "success", "nickname": nickname}

@router.get("/api/products")
async def get_public_products():
    rows = await db_query(
        """
        SELECT sku, provider, name, cost_price, price, category,
               description, image_url, logo_url, promo_title, promo_text, promo_badge, promo_url, display_order
        FROM products
        WHERE active=1
        ORDER BY COALESCE(category, ''), COALESCE(provider, ''), COALESCE(display_order, 0), price
        """
    )

    active_promos = await _get_active_price_promos()
    now_utc = datetime.now(timezone.utc)

    categories: Dict[str, Dict[str, Any]] = {}
    flat_products = []

    for row in rows:
        sku, provider, name, cost_price, price, category, description, image_url, logo_url, promo_title, promo_text, promo_badge, promo_url, display_order = row
        normalized_provider = normalized_provider_name(provider, name, category)
        normalized_category = normalized_category_name(category, normalized_provider)
        product = {
            "sku": sku,
            "provider": normalized_provider,
            "name": name,
            "cost": float(cost_price or 0),
            "price": float(price or 0),
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

        product = _apply_best_price_promo(product, active_promos, now_utc)
        flat_products.append(product)

        category_name = product["category"]
        provider_name = product["provider"]
        category_entry = categories.setdefault(category_name, {"name": category_name, "providers": {}})
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


@router.get("/api/promos/active")
async def get_active_promos() -> Dict[str, Any]:
    rows = await db_query(
        """
        SELECT id, title, code, description, badge, cta_text, cta_url, image_url,
               rule_type, target_scope, target_value, discount_type, discount_value, max_discount,
             starts_at, ends_at, show_on_website, active
        FROM promos
         WHERE active=1 AND COALESCE(show_on_website, 1)=1
        ORDER BY created_at DESC
        """
    )

    now_utc = datetime.now(timezone.utc)
    promos: list[Dict[str, Any]] = []
    for row in rows:
        starts_at = _to_utc_datetime(row[14])
        ends_at = _to_utc_datetime(row[15])
        if starts_at and now_utc < starts_at:
            continue
        if ends_at and now_utc > ends_at:
            continue

        promos.append(
            {
                "id": row[0],
                "title": row[1] or "",
                "code": row[2] or "",
                "description": row[3] or "",
                "badge": row[4] or "",
                "cta_text": row[5] or "Lihat Promo",
                "cta_url": row[6] or "#",
                "image_url": row[7] or "",
                "rule_type": row[8] or "content",
                "target_scope": row[9] or "all",
                "target_value": row[10] or "",
                "discount_type": row[11] or "",
                "discount_value": float(row[12] or 0),
                "max_discount": float(row[13] or 0),
                "starts_at": row[14].isoformat() if hasattr(row[14], "isoformat") else row[14],
                "ends_at": row[15].isoformat() if hasattr(row[15], "isoformat") else row[15],
                "show_on_website": bool(row[16]),
            }
        )

    return {"promos": promos}


@router.get("/api/promos/validate")
async def validate_promo(sku: str, code: str) -> Dict[str, Any]:
    cleaned_code = (code or "").strip()
    if not cleaned_code:
        raise HTTPException(status_code=400, detail="Kode promo wajib diisi")

    row = await db_query(
        """
        SELECT sku, provider, name, price, category
        FROM products
        WHERE sku=:sku AND active=1
        """,
        {"sku": sku},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Produk tidak ditemukan")

    sku_value, provider, name, base_price, category = row[0]
    normalized_provider = normalized_provider_name(provider, name, category)
    product = {
        "sku": sku_value,
        "provider": normalized_provider,
        "name": name,
        "price": float(base_price or 0),
        "category": normalized_category_name(category, normalized_provider),
    }

    active_promos = await _get_active_price_promos()
    now_utc = datetime.now(timezone.utc)
    resolved = _apply_best_price_promo(product, active_promos, now_utc, promo_code=cleaned_code)
    base_value = float(base_price or 0)
    final_price = float(resolved.get("price") or 0)

    if final_price >= base_value:
        normalized_input_code = cleaned_code.lower()
        matching_code_promos = [
            promo
            for promo in active_promos
            if str(promo.get("code") or "").strip().lower() == normalized_input_code and _promo_matches(promo, product, now_utc)
        ]

        if matching_code_promos:
            if all((promo.get("rule_type") or "").lower() != "price" for promo in matching_code_promos):
                raise HTTPException(
                    status_code=400,
                    detail="Kode promo ditemukan, tapi belum diset sebagai promo harga (rule_type=price)",
                )
            raise HTTPException(
                status_code=400,
                detail="Kode promo ditemukan, tapi pengaturan diskonnya belum valid",
            )
        raise HTTPException(status_code=400, detail="Kode promo tidak valid untuk produk ini")

    promo_applied = resolved.get("promo_applied") or {}
    return {
        "valid": True,
        "sku": sku_value,
        "code": cleaned_code,
        "original_price": base_value,
        "final_price": final_price,
        "discount_amount": round(base_value - final_price, 2),
        "promo": promo_applied,
    }

@router.post("/callback")
async def tripay_callback(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Callback-Signature")

    expected_sig = hmac.new(TRIPAY_PRIVATE_KEY.encode(), raw_body, hashlib.sha256).hexdigest()

    if signature != expected_sig:
        return {"success": False, "message": "Invalid signature"}

    data = json.loads(raw_body)
    merchant_ref = data.get("merchant_ref")
    status = data.get("status")

    if status == "PAID":
        order = await db_query("SELECT nominal, target_id, payment_status FROM topup WHERE id=:id", {"id": merchant_ref})
        
        if order:
            sku, target_id, current_status = order[0]
            if current_status != "PAID":
                await db_execute(
                    "UPDATE topup SET payment_status='PAID', topup_status='PROCESSING' WHERE id=:id",
                    {"id": merchant_ref}
                )
                print(f"🚀 Tripay LUNAS! Nembak Digiflazz untuk Ref: {merchant_ref}")
                # WAJIB AWAIT
                await kirim_digiflazz(sku=sku, tujuan=target_id, ref_id=merchant_ref)

    return {"success": True}

@router.post("/api/webhook/digiflazz")
async def digiflazz_webhook(request: Request, x_hub_signature: str = Header(None, alias="X-Hub-Signature")):
    body = await request.body()
    my_signature = "sha1=" + hmac.new(DIGIFLAZZ_SECRET.encode(), body, hashlib.sha1).hexdigest()
    
    if my_signature != x_hub_signature:
        raise HTTPException(status_code=403, detail="Signature tidak valid!")

    data = await request.json()
    payload = data.get("data", {})
    
    ref_id = payload.get("ref_id")
    status = payload.get("status")
    sn = payload.get("sn", "")
    
    if status == "Sukses":
        await db_execute(
            "UPDATE topup SET topup_status='SUCCESS', sn=:sn, note=:note WHERE id=:id", 
            {"sn": sn, "note": "Sukses", "id": ref_id}
        )
        print(f"✅ TOPUP SUKSES! Ref: {ref_id} | SN: {sn}")
    elif status == "Gagal":
        pesan_error = payload.get("message", "Gagal dari provider")
        await db_execute(
            "UPDATE topup SET topup_status='FAILED', note=:note WHERE id=:id", 
            {"note": pesan_error, "id": ref_id}
        )
        print(f"❌ TOPUP GAGAL! Ref: {ref_id} | Error: {pesan_error}")

    return {"message": "Webhook Digiflazz diterima"}

@router.get("/callback")
def tripay_return():
    return RedirectResponse(url="/")

@router.post("/topup/{identifier}/cancel")
async def cancel_transaction(identifier: str):
    try:
        await db_execute(
            "UPDATE topup SET payment_status='CANCELED', topup_status='FAILED' WHERE id=:id", 
            {"id": identifier}
        )
        return {"success": True, "message": "Transaksi berhasil dibatalkan"}
    except Exception as e:
        print(f"🚨 ERROR CANCEL: {e}")
        raise HTTPException(500, f"Gagal membatalkan transaksi: {str(e)}")
