import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import text

from app.core.database import get_engine
from app.core.settings import settings
from app.core.write_quiescence import WriteQuiescenceActive, write_quiescence
from app.database import db_execute, db_execute_rowcount, db_query, init_db
from app.promotions.service import (
    finalize_order_promotions,
    release_expired_reservations,
    release_order_promotions,
)
from app.services.catalog_sync_service import sync_digiflazz_products
from app.services.digiflazz_service import (
    DIGIFLAZZ_OUTCOME_DEFINITIVE_FAILURE,
    DIGIFLAZZ_OUTCOME_PENDING,
    DIGIFLAZZ_OUTCOME_RETRYABLE_NOT_SENT,
    DIGIFLAZZ_OUTCOME_SENT_UNKNOWN,
    DIGIFLAZZ_OUTCOME_SUCCESS,
    cek_status_digiflazz,
    cek_status_postpaid,
    classify_digiflazz_transaction_response,
    digiflazz_response_data,
    get_digiflazz_balance,
    kirim_digiflazz,
    pay_postpaid,
)
from app.services.provider_settings import get_provider_runtime_settings
from app.services.tripay_service import (
    check_transaction_status,
    list_merchant_transactions,
    list_open_payment_transactions,
)
from app.services.wallet_service import credit_wallet_deposit_if_new


MAX_PROVIDER_RETRY = 3
MAX_DIGIFLAZZ_PREPAID_STATUS_LOOKUP_AGE_DAYS = 90
MIN_PROVIDER_STATUS_INTERVAL_SECONDS = 60
MAX_PAYMENT_RECONCILE_PER_TICK = 10
PAYMENT_RECONCILE_INTERVAL_SECONDS = 60
MAX_WALLET_RECONCILE_PER_TICK = 10
MAX_OPEN_PAYMENT_RECONCILE_PER_TICK = 10
DIGIFLAZZ_BALANCE_CHECK_INTERVAL_SECONDS = 300
DIGIFLAZZ_BALANCE_ALERT_INTERVAL_SECONDS = 3600
PROVIDER_CLAIM_LEASE_SECONDS = 300
PROVIDER_RECONCILE_INTERVAL_SECONDS = 60

_last_balance_check_at: Optional[datetime] = None
_last_balance_alert_at: Optional[datetime] = None
_last_product_sync_at: Optional[datetime] = None


def _float_value(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalized_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalized_payment_status(value: Any) -> str:
    return str(value or "").strip().upper()


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _provider_payload_values(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider_rc": data.get("rc") or "",
        "provider_price": _float_value(data.get("price")),
        "provider_selling_price": _float_value(data.get("selling_price")),
        "provider_last_balance": _float_value(data.get("buyer_last_saldo")),
        "provider_payload": _json_dumps(data),
    }


async def _claim_provider_order(order_id: str) -> Optional[str]:
    lease_id = str(uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=PROVIDER_CLAIM_LEASE_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")
    await init_db()
    async with get_engine().begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE topup
                SET provider_claim_id=:lease_id,
                    provider_claimed_at=CURRENT_TIMESTAMP,
                    provider_claim_expires_at=:expires_at,
                    status_updated_at=CURRENT_TIMESTAMP
                WHERE id=:id
                  AND payment_status='PAID'
                  AND topup_status='PROCESSING'
                  AND COALESCE(provider_outcome, 'NOT_SENT')='NOT_SENT'
                  AND (
                    provider_claim_expires_at IS NULL
                    OR provider_claim_expires_at <= CURRENT_TIMESTAMP
                  )
                """
            ),
            {"id": order_id, "lease_id": lease_id, "expires_at": expires_at},
        )
        return lease_id if result.rowcount == 1 else None


async def claim_provider_status_reconciliation(order_id: str) -> Optional[str]:
    """Return a fenced lease for one safe provider status lookup, if available.

    The lease is recorded before the network call.  Every subsequent result
    write must carry this token, so a newer webhook or worker result revokes a
    delayed lookup instead of letting it overwrite newer state.
    """

    lease_id = str(uuid4())
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=PROVIDER_CLAIM_LEASE_SECONDS)
    ).strftime("%Y-%m-%d %H:%M:%S")
    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(seconds=PROVIDER_RECONCILE_INTERVAL_SECONDS)
    ).replace(tzinfo=None)
    updated_rows = await db_execute_rowcount(
        """
        UPDATE topup
        SET provider_claim_id=:lease_id,
            provider_claimed_at=CURRENT_TIMESTAMP,
            provider_claim_expires_at=:expires_at,
            provider_last_check_at=CURRENT_TIMESTAMP,
            status_updated_at=CURRENT_TIMESTAMP
        WHERE id=:id
          AND payment_status='PAID'
          AND COALESCE(topup_status, '') IN ('PROCESSING', 'PENDING_PROVIDER')
          AND (
              COALESCE(provider_outcome, '') IN ('SENT_UNKNOWN', 'PENDING_PROVIDER')
              OR (
                  COALESCE(topup_status, '')='PENDING_PROVIDER'
                  AND COALESCE(provider_outcome, 'NOT_SENT')='NOT_SENT'
              )
          )
          AND (
              provider_last_check_at IS NULL
              OR provider_last_check_at <= :cutoff
          )
          AND (
              provider_claim_expires_at IS NULL
              OR provider_claim_expires_at <= CURRENT_TIMESTAMP
          )
        """,
        {
            "id": order_id,
            "lease_id": lease_id,
            "expires_at": expires_at,
            "cutoff": cutoff,
        },
    )
    return lease_id if updated_rows else None


async def release_provider_status_reconciliation(order_id: str, lease_id: str) -> None:
    """Release only the caller's status-lookup lease after a local failure."""

    await db_execute(
        """
        UPDATE topup
        SET provider_claim_id=NULL,
            provider_claimed_at=NULL,
            provider_claim_expires_at=NULL,
            status_updated_at=CURRENT_TIMESTAMP
        WHERE id=:id AND provider_claim_id=:lease_id
        """,
        {"id": order_id, "lease_id": lease_id},
    )


def _provider_request_snapshot(order_id: str, sku: str, phone: str, order_type: str) -> str:
    return _json_dumps(
        {
            "order_id": order_id,
            "ref_id": order_id,
            "buyer_sku_code": sku,
            "customer_no": phone,
            "order_type": order_type,
        }
    )


async def _start_provider_attempt(order_id: str, sku: str, phone: str, order_type: str) -> int:
    rows = await db_query(
        """
        SELECT COALESCE(MAX(attempt_number), 0)
        FROM provider_attempts
        WHERE provider='digiflazz' AND order_id=:order_id
        """,
        {"order_id": order_id},
    )
    attempt_number = int(rows[0][0] or 0) + 1 if rows else 1
    await db_execute(
        """
        INSERT INTO provider_attempts (
            order_id, provider, attempt_number, ref_id, request_payload,
            request_state, local_outcome
        )
        VALUES (
            :order_id, 'digiflazz', :attempt_number, :ref_id, :request_payload,
            'SENDING', 'SENDING'
        )
        """,
        {
            "order_id": order_id,
            "attempt_number": attempt_number,
            "ref_id": order_id,
            "request_payload": _provider_request_snapshot(order_id, sku, phone, order_type),
        },
    )
    return attempt_number


async def _finish_provider_attempt(
    order_id: str,
    attempt_number: int,
    *,
    request_state: str,
    local_outcome: str,
    provider_status: str = "",
    provider_transaction_id: str = "",
    provider_response: Optional[dict[str, Any]] = None,
    error_type: str = "",
    error_message: str = "",
    reconciled: bool = False,
) -> None:
    await db_execute(
        """
        UPDATE provider_attempts
        SET request_state=:request_state,
            response_at=COALESCE(response_at, CURRENT_TIMESTAMP),
            provider_transaction_id=:provider_transaction_id,
            provider_status=:provider_status,
            local_outcome=:local_outcome,
            provider_response=:provider_response,
            error_type=:error_type,
            error_message=:error_message,
            reconciled_at=CASE WHEN :reconciled=1 THEN CURRENT_TIMESTAMP ELSE reconciled_at END,
            updated_at=CURRENT_TIMESTAMP
        WHERE provider='digiflazz' AND order_id=:order_id AND attempt_number=:attempt_number
        """,
        {
            "order_id": order_id,
            "attempt_number": attempt_number,
            "request_state": request_state,
            "local_outcome": local_outcome,
            "provider_status": provider_status,
            "provider_transaction_id": provider_transaction_id,
            "provider_response": _json_dumps(provider_response or {}),
            "error_type": error_type,
            "error_message": error_message,
            "reconciled": 1 if reconciled else 0,
        },
    )


async def _discard_stale_provider_attempt(order_id: str, attempt_number: int) -> None:
    """Close an attempt whose worker no longer owns the order lease.

    The response is deliberately not recorded as a completed provider result:
    a callback or another lease owner may already have established the order's
    outcome.  Keeping an explicit discarded audit record is safer than leaving
    an ambiguous ``SENDING`` attempt behind.
    """

    await _finish_provider_attempt(
        order_id,
        attempt_number,
        request_state="DISCARDED",
        local_outcome="STALE_DISCARDED",
        error_type="ProviderLeaseLost",
        error_message="Respons provider diabaikan karena lease worker sudah tidak valid",
    )


async def _record_safe_provider_retry(
    order_id: str,
    lease_id: str,
    retry_count: int,
    values: dict[str, Any],
    error_message: str,
) -> tuple[bool, bool]:
    """Record a retry only when the adapter proved no external send occurred."""

    next_retry_count = retry_count + 1
    exhausted = next_retry_count >= MAX_PROVIDER_RETRY
    updated = await db_execute_rowcount(
        """
        UPDATE topup
        SET topup_status=:topup_status,
            provider_outcome=:provider_outcome,
            provider_retry_count=:provider_retry_count,
            provider_last_error=:error,
            provider_rc=:provider_rc,
            provider_price=:provider_price,
            provider_selling_price=:provider_selling_price,
            provider_last_balance=:provider_last_balance,
            provider_last_check_at=CURRENT_TIMESTAMP,
            provider_claim_id=NULL,
            provider_claimed_at=NULL,
            provider_claim_expires_at=NULL,
            provider_payload=:provider_payload,
            status_updated_at=CURRENT_TIMESTAMP
        WHERE id=:id
          AND provider_claim_id=:lease_id
          AND provider_claim_expires_at > CURRENT_TIMESTAMP
          AND payment_status='PAID'
          AND topup_status='PROCESSING'
          AND provider_outcome='SENT_UNKNOWN'
        """,
        {
            "id": order_id,
            "lease_id": lease_id,
            "topup_status": "FAILED" if exhausted else "PROCESSING",
            # A retry-exhausted pre-send failure is still known not to have
            # reached Digiflazz. Keep that proof so an admin can safely retry
            # after fixing the local cause.
            "provider_outcome": "NOT_SENT",
            "provider_retry_count": next_retry_count,
            "error": error_message,
            **values,
        },
    )
    return bool(updated), bool(updated and exhausted)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo:
            return value.astimezone(timezone.utc)
        return value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo:
            return parsed.astimezone(timezone.utc)
        return parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _recently_checked(value: Any, seconds: int = MIN_PROVIDER_STATUS_INTERVAL_SECONDS) -> bool:
    parsed = _parse_datetime(value)
    if not parsed:
        return False
    return datetime.now(timezone.utc) - parsed < timedelta(seconds=seconds)


def _can_reconcile_digiflazz_status(order_type: Any, created_at: Any) -> bool:
    """Avoid a prepaid status lookup that Digiflazz could turn into a new top-up."""

    if str(order_type or "PREPAID").upper() == "POSTPAID":
        return True
    created = _parse_datetime(created_at)
    if created is None:
        return False
    return datetime.now(timezone.utc) - created < timedelta(days=MAX_DIGIFLAZZ_PREPAID_STATUS_LOOKUP_AGE_DAYS)


def _tripay_data(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    data = response.get("data")
    if isinstance(data, dict):
        return data
    return {}


def _tripay_transaction_list(response: Any) -> list[dict[str, Any]]:
    if not isinstance(response, dict):
        return []
    data = response.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("data", "transactions", "items"):
            items = data.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    return []


async def reconcile_payment_engine() -> None:
    try:
        rows = await db_query(
            """
            SELECT id, payment_reference, payment_status, topup_status,
                   payment_expired_at, payment_last_check_at, invoice_url,
                   COALESCE(payment_creation_outcome, ''), COALESCE(amount, 0)
            FROM topup
            WHERE payment_status='UNPAID'
               OR (
                    payment_status IN ('CANCELED', 'CANCELLED')
                    AND COALESCE(topup_status, '')='FAILED'
                    AND COALESCE(refund_status, '') != 'PAYMENT_RECEIVED_AFTER_CANCEL'
               )
            ORDER BY created_at ASC
            LIMIT 50
            """
        )
    except Exception as exc:
        logging.error(f"ENGINE DB ERROR (payment reconcile): {exc}")
        return

    checked_count = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        (
            order_id,
            payment_reference,
            _payment_status,
            _topup_status,
            raw_payment_expired_at,
            payment_last_check_at,
            _invoice_url,
            payment_creation_outcome,
            expected_amount,
        ) = row
        payment_expired_at = _parse_datetime(raw_payment_expired_at)

        if (
            str(_payment_status or "").upper() == "UNPAID"
            and payment_expired_at
            and payment_expired_at <= now
        ):
            try:
                updated = await db_execute_rowcount(
                    """
                    UPDATE topup
                    SET payment_status='EXPIRED',
                        topup_status='FAILED',
                        payment_last_check_at=CURRENT_TIMESTAMP,
                        note=CASE
                            WHEN COALESCE(note, '')='' THEN 'Invoice Tripay expired otomatis'
                            ELSE note
                        END,
                        status_updated_at=CURRENT_TIMESTAMP
                    WHERE id=:id AND payment_status='UNPAID'
                    """,
                    {"id": order_id},
                )
                if updated:
                    await release_order_promotions(str(order_id))
                    print(f"INFO Order {order_id} ditandai expired otomatis.")
            except Exception as exc:
                logging.error(f"ENGINE DB ERROR (expire invoice {order_id}): {exc}")
            continue

        if _recently_checked(payment_last_check_at, PAYMENT_RECONCILE_INTERVAL_SECONDS):
            continue
        if not payment_reference and str(payment_creation_outcome or "").upper() != "SENT_UNKNOWN":
            continue
        if checked_count >= MAX_PAYMENT_RECONCILE_PER_TICK:
            break

        checked_count += 1
        try:
            if payment_reference:
                response = await check_transaction_status(str(payment_reference))
                tripay_data = _tripay_data(response)
            else:
                response = await list_merchant_transactions(
                    merchant_ref=str(order_id),
                    page=1,
                    per_page=10,
                )
                tripay_data = next(
                    (
                        item
                        for item in _tripay_transaction_list(response)
                        if str(item.get("merchant_ref") or "") == str(order_id)
                    ),
                    {},
                )
            status = _normalized_payment_status(
                tripay_data.get("status")
                or (
                    response.get("status")
                    if payment_reference and isinstance(response, dict)
                    else ""
                )
            )
            if not status:
                await db_execute(
                    """
                    UPDATE topup
                    SET payment_last_check_at=CURRENT_TIMESTAMP,
                        tripay_payload=:tripay_payload,
                        status_updated_at=CURRENT_TIMESTAMP
                    WHERE id=:id
                      AND payment_status IN ('UNPAID', 'CANCELED', 'CANCELLED')
                    """,
                    {"id": order_id, "tripay_payload": _json_dumps(response)},
                )
                continue

            if status == "PAID":
                reported_amount = tripay_data.get("amount") or tripay_data.get("amount_received")
                if (
                    _int_value(expected_amount) > 0
                    and _int_value(reported_amount) != _int_value(expected_amount)
                ):
                    logging.warning(
                        "Tripay payment amount mismatch order=%s expected=%s",
                        order_id,
                        _int_value(expected_amount),
                    )
                    await db_execute(
                        """
                        UPDATE topup
                        SET payment_last_check_at=CURRENT_TIMESTAMP,
                            tripay_payload=:tripay_payload,
                            status_updated_at=CURRENT_TIMESTAMP
                        WHERE id=:id
                          AND payment_status IN ('UNPAID', 'CANCELED', 'CANCELLED')
                        """,
                        {"id": order_id, "tripay_payload": _json_dumps(response)},
                    )
                    continue
                if str(_payment_status or "").upper() in {"CANCELED", "CANCELLED"}:
                    updated = await db_execute_rowcount(
                        """
                        UPDATE topup
                        SET payment_reference=COALESCE(NULLIF(:payment_reference, ''), payment_reference),
                            payment_creation_outcome='SUCCESS',
                            payment_last_check_at=CURRENT_TIMESTAMP,
                            tripay_payload=:tripay_payload,
                            refund_status='PAYMENT_RECEIVED_AFTER_CANCEL',
                            refund_note=CASE
                                WHEN COALESCE(refund_note, '')='' THEN
                                    'Pembayaran diterima setelah pembatalan customer; review refund manual diperlukan'
                                ELSE refund_note
                            END,
                            status_updated_at=CURRENT_TIMESTAMP
                        WHERE id=:id
                          AND payment_status IN ('CANCELED', 'CANCELLED')
                          AND COALESCE(topup_status, '')='FAILED'
                          AND COALESCE(refund_status, '') != 'PAYMENT_RECEIVED_AFTER_CANCEL'
                        """,
                        {
                            "id": order_id,
                            "payment_reference": tripay_data.get("reference") or payment_reference,
                            "tripay_payload": _json_dumps(response),
                        },
                    )
                    if updated:
                        logging.warning(
                            "Tripay payment received after cancellation order=%s; fulfillment remains blocked",
                            order_id,
                        )
                    continue
                updated = await db_execute_rowcount(
                    """
                    UPDATE topup
                    SET payment_status='PAID',
                        topup_status=CASE
                            WHEN COALESCE(topup_status, '') IN ('', 'PENDING_PAYMENT') THEN 'PROCESSING'
                            ELSE topup_status
                        END,
                        payment_reference=COALESCE(NULLIF(:payment_reference, ''), payment_reference),
                        payment_creation_outcome='SUCCESS',
                        payment_last_check_at=CURRENT_TIMESTAMP,
                        tripay_payload=:tripay_payload,
                        status_updated_at=CURRENT_TIMESTAMP
                    WHERE id=:id AND payment_status='UNPAID'
                    """,
                    {
                        "id": order_id,
                        "payment_reference": tripay_data.get("reference") or payment_reference,
                        "tripay_payload": _json_dumps(response),
                    },
                )
                if updated:
                    await finalize_order_promotions(str(order_id))
                    phone_rows = await db_query("SELECT phone FROM topup WHERE id=:id", {"id": order_id})
                    if phone_rows and phone_rows[0][0]:
                        await db_execute(
                            """
                            INSERT INTO notification_outbox (
                                channel, recipient, subject, body, status,
                                reference_type, reference_id, event_key
                            )
                            VALUES ('WHATSAPP', :recipient, 'Pembayaran diterima', :body, 'QUEUED', 'topup', :reference_id, :event_key)
                            ON CONFLICT(event_key) DO NOTHING
                            """,
                            {
                                "recipient": phone_rows[0][0],
                                "body": f"Pembayaran order {order_id} diterima. Topup sedang diproses.",
                                "reference_id": order_id,
                                "event_key": f"WHATSAPP:topup:{order_id}:Pembayaran diterima",
                            },
                        )
                print(f"OK Order {order_id} PAID dari rekonsiliasi Tripay.")
            elif status in {"EXPIRED", "FAILED", "REFUND"}:
                updated = await db_execute_rowcount(
                    """
                    UPDATE topup
                    SET payment_status=:payment_status,
                        topup_status=CASE
                            WHEN :payment_status IN ('EXPIRED', 'FAILED') THEN 'FAILED'
                            ELSE topup_status
                        END,
                        payment_reference=COALESCE(NULLIF(:payment_reference, ''), payment_reference),
                        payment_creation_outcome='SUCCESS',
                        payment_last_check_at=CURRENT_TIMESTAMP,
                        tripay_payload=:tripay_payload,
                        status_updated_at=CURRENT_TIMESTAMP
                    WHERE id=:id AND payment_status='UNPAID'
                    """,
                    {
                        "id": order_id,
                        "payment_status": status,
                        "payment_reference": tripay_data.get("reference") or payment_reference,
                        "tripay_payload": _json_dumps(response),
                    },
                )
                if updated:
                    await release_order_promotions(
                        str(order_id),
                        include_redeemed=status == "REFUND",
                    )
                    print(f"INFO Order {order_id} status pembayaran Tripay: {status}.")
            else:
                await db_execute(
                    """
                    UPDATE topup
                    SET payment_last_check_at=CURRENT_TIMESTAMP,
                        tripay_payload=:tripay_payload,
                        status_updated_at=CURRENT_TIMESTAMP
                    WHERE id=:id
                      AND payment_status IN ('UNPAID', 'CANCELED', 'CANCELLED')
                    """,
                    {"id": order_id, "tripay_payload": _json_dumps(response)},
                )
        except Exception as exc:
            logging.error(
                "Error rekonsiliasi Tripay order=%s error_type=%s",
                order_id,
                type(exc).__name__,
            )


async def reconcile_wallet_deposit_engine() -> None:
    try:
        runtime_settings = await get_provider_runtime_settings()
        interval_seconds = max(
            30,
            _int_value(runtime_settings.get("tripay_wallet_sync_interval_seconds"), 120),
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=interval_seconds)).replace(tzinfo=None)
        rows = await db_query(
            """
            SELECT id, customer_id, reference, merchant_ref, last_check_at
            FROM customer_wallet_deposits
            WHERE status='PENDING'
              AND (last_check_at IS NULL OR last_check_at <= :cutoff)
            ORDER BY created_at ASC
            LIMIT :limit
            """,
            {"cutoff": cutoff, "limit": MAX_WALLET_RECONCILE_PER_TICK},
        )
    except Exception as exc:
        logging.error(f"ENGINE DB ERROR (wallet deposit reconcile): {exc}")
        return

    for deposit_id, customer_id, reference, merchant_ref, _last_check_at in rows:
        try:
            if reference:
                response = await check_transaction_status(str(reference))
                tripay_data = _tripay_data(response)
            else:
                response = await list_merchant_transactions(
                    merchant_ref=str(merchant_ref),
                    page=1,
                    per_page=10,
                )
                tripay_data = next(
                    (
                        item
                        for item in _tripay_transaction_list(response)
                        if str(item.get("merchant_ref") or "") == str(merchant_ref)
                    ),
                    {},
                )
            if not tripay_data:
                tripay_data = {"reference": reference, "merchant_ref": merchant_ref, "raw": response}
            tripay_data.setdefault("reference", reference)
            tripay_data.setdefault("merchant_ref", merchant_ref)
            status = _normalized_payment_status(
                tripay_data.get("status") or (response.get("status") if isinstance(response, dict) else "")
            )

            if status == "PAID":
                credited = await credit_wallet_deposit_if_new(int(customer_id), tripay_data)
                print(f"OK Deposit wallet {reference} dikreditkan dari rekonsiliasi Tripay." if credited else f"INFO Deposit wallet {reference} sudah dikreditkan.")
            elif status in {"EXPIRED", "FAILED", "REFUND"}:
                await db_execute(
                    """
                    UPDATE customer_wallet_deposits
                    SET status=:status,
                        reference=COALESCE(NULLIF(:reference, ''), reference),
                        payload=:payload,
                        last_check_at=CURRENT_TIMESTAMP
                    WHERE id=:id
                    """,
                    {
                        "id": deposit_id,
                        "status": status,
                        "reference": tripay_data.get("reference") or "",
                        "payload": _json_dumps(tripay_data or response),
                    },
                )
            else:
                await db_execute(
                    """
                    UPDATE customer_wallet_deposits
                    SET reference=COALESCE(NULLIF(:reference, ''), reference),
                        payload=:payload,
                        last_check_at=CURRENT_TIMESTAMP
                    WHERE id=:id
                    """,
                    {
                        "id": deposit_id,
                        "reference": tripay_data.get("reference") or "",
                        "payload": _json_dumps(tripay_data or response),
                    },
                )
        except Exception as exc:
            logging.error(
                "Error rekonsiliasi deposit wallet Tripay merchant_ref=%s error_type=%s",
                merchant_ref,
                type(exc).__name__,
            )


async def reconcile_open_payment_engine() -> None:
    try:
        runtime_settings = await get_provider_runtime_settings()
        interval_seconds = max(
            30,
            _int_value(runtime_settings.get("tripay_wallet_sync_interval_seconds"), 120),
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=interval_seconds)).replace(tzinfo=None)
        rows = await db_query(
            """
            SELECT id, customer_id, uuid, merchant_ref, open_payment_last_check_at
            FROM customer_open_payments
            WHERE active=1
              AND COALESCE(uuid, '') != ''
              AND (open_payment_last_check_at IS NULL OR open_payment_last_check_at <= :cutoff)
            ORDER BY COALESCE(open_payment_last_check_at, created_at) ASC
            LIMIT :limit
            """,
            {"cutoff": cutoff, "limit": MAX_OPEN_PAYMENT_RECONCILE_PER_TICK},
        )
    except Exception as exc:
        logging.error(f"ENGINE DB ERROR (open payment reconcile): {exc}")
        return

    for payment_id, customer_id, uuid_value, merchant_ref, _last_check_at in rows:
        credited_count = 0
        message = "Tidak ada transaksi baru"
        try:
            response = await list_open_payment_transactions(str(uuid_value))
            if not isinstance(response, dict) or not response.get("success"):
                message = str((response or {}).get("message") if isinstance(response, dict) else response or "Gagal cek Open Payment")
            else:
                for transaction in _tripay_transaction_list(response):
                    status = _normalized_payment_status(transaction.get("status"))
                    if status and status not in {"PAID", "SUCCESS", "SETTLED"}:
                        continue
                    transaction.setdefault("merchant_ref", merchant_ref)
                    if await credit_wallet_deposit_if_new(int(customer_id), transaction):
                        credited_count += 1
                message = f"{credited_count} transaksi dikreditkan" if credited_count else "Tidak ada transaksi baru"

            await db_execute(
                """
                UPDATE customer_open_payments
                SET open_payment_last_check_at=CURRENT_TIMESTAMP,
                    open_payment_last_sync_message=:message,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=:id
                """,
                {"id": payment_id, "message": message[:255]},
            )
            if credited_count:
                print(f"OK Open Payment Tripay {uuid_value}: {credited_count} deposit dikreditkan.")
        except Exception as exc:
            logging.error(f"Error rekonsiliasi Open Payment Tripay {uuid_value}: {exc}")


async def product_catalog_sync_engine() -> None:
    async with write_quiescence.writer_section("product_catalog_sync_engine"):
        await _product_catalog_sync_engine()


async def _product_catalog_sync_engine() -> None:
    global _last_product_sync_at

    try:
        runtime_settings = await get_provider_runtime_settings()
        if not _truthy(runtime_settings.get("digiflazz_product_auto_sync_enabled")):
            return

        interval_minutes = max(
            30,
            _int_value(runtime_settings.get("digiflazz_product_sync_interval_minutes"), 360),
        )
        now = datetime.now(timezone.utc)
        if _last_product_sync_at and now - _last_product_sync_at < timedelta(minutes=interval_minutes):
            return
        _last_product_sync_at = now

        result = await sync_digiflazz_products()
        await db_execute(
            """
            INSERT INTO site_settings (key, value, updated_by)
            VALUES (:key, :value, 'engine')
            ON CONFLICT(key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_by = EXCLUDED.updated_by,
                updated_at = CURRENT_TIMESTAMP
            """,
            {"key": "provider.digiflazz_last_product_sync", "value": _json_dumps(result)},
        )
        if result.get("errors"):
            logging.warning("Auto-sync produk Digiflazz selesai dengan error: %s", "; ".join(result["errors"]))
        else:
            print(f"OK Auto-sync produk Digiflazz: {result.get('processed', 0)} produk diproses.")
    except Exception as exc:
        logging.error(f"Error auto-sync produk Digiflazz: {exc}")


async def provider_balance_watch_engine() -> None:
    global _last_balance_check_at, _last_balance_alert_at

    now = datetime.now(timezone.utc)
    if _last_balance_check_at and now - _last_balance_check_at < timedelta(seconds=DIGIFLAZZ_BALANCE_CHECK_INTERVAL_SECONDS):
        return
    _last_balance_check_at = now

    try:
        runtime_settings = await get_provider_runtime_settings()
        threshold = _float_value(runtime_settings.get("digiflazz_low_balance_threshold") or 0)
        if threshold <= 0:
            return

        response = await get_digiflazz_balance()
        data = response.get("data") if isinstance(response, dict) else {}
        if not isinstance(data, dict) or "deposit" not in data:
            return

        deposit = _float_value(data.get("deposit"))
        if deposit >= threshold:
            return
        if _last_balance_alert_at and now - _last_balance_alert_at < timedelta(seconds=DIGIFLAZZ_BALANCE_ALERT_INTERVAL_SECONDS):
            return

        since = (now - timedelta(seconds=DIGIFLAZZ_BALANCE_ALERT_INTERVAL_SECONDS)).replace(tzinfo=None)
        existing_alert = await db_query(
            """
            SELECT id
            FROM notification_outbox
            WHERE reference_type='provider_balance'
              AND reference_id='digiflazz'
              AND created_at >= :since
            LIMIT 1
            """,
            {"since": since},
        )
        if existing_alert:
            _last_balance_alert_at = now
            return

        _last_balance_alert_at = now
        await db_execute(
            """
            INSERT INTO notification_outbox (
                channel, recipient, subject, body, status,
                reference_type, reference_id
            )
            VALUES (
                'SYSTEM', 'admin', 'Saldo Digiflazz rendah',
                :body, 'QUEUED', 'provider_balance', 'digiflazz'
            )
            """,
            {
                "body": (
                    f"Saldo Digiflazz saat ini {deposit:,.0f}, "
                    f"di bawah threshold {threshold:,.0f}. Segera buat tiket deposit."
                )
            },
        )
        print("WARN Saldo Digiflazz di bawah threshold, notifikasi admin dibuat.")
    except Exception as exc:
        logging.error(f"Error cek saldo Digiflazz: {exc}")


async def _reconcile_unknown_provider_orders() -> None:
    rows = await db_query(
        """
        SELECT id, COALESCE(target_id, phone), nominal,
               COALESCE(order_type, 'PREPAID'), provider_last_check_at, created_at
        FROM topup
        WHERE payment_status='PAID'
          AND COALESCE(provider_outcome, '')='SENT_UNKNOWN'
          AND topup_status IN ('PROCESSING', 'PENDING_PROVIDER')
        """
    )
    for order_id, phone, sku, order_type, last_checked, created_at in rows:
        if _recently_checked(last_checked, PROVIDER_RECONCILE_INTERVAL_SECONDS):
            continue
        if not _can_reconcile_digiflazz_status(order_type, created_at):
            await db_execute(
                """
                UPDATE topup
                SET provider_last_check_at=CURRENT_TIMESTAMP,
                    provider_last_error='Rekonsiliasi Digiflazz prabayar diblokir setelah 90 hari untuk mencegah transaksi baru',
                    status_updated_at=CURRENT_TIMESTAMP
                WHERE id=:id AND provider_outcome='SENT_UNKNOWN'
                """,
                {"id": order_id},
            )
            continue
        lease_id = await claim_provider_status_reconciliation(str(order_id))
        if not lease_id:
            continue
        try:
            if str(order_type or "PREPAID").upper() == "POSTPAID":
                response = await cek_status_postpaid(sku, phone, order_id)
            else:
                response = await cek_status_digiflazz(sku, phone, order_id)
            data = digiflazz_response_data(response)
            status = _normalized_status(data.get("status"))
            values = _provider_payload_values(data)
            classification = classify_digiflazz_transaction_response(response)
            if classification == DIGIFLAZZ_OUTCOME_SUCCESS:
                next_status, outcome = "SUCCESS", "SUCCESS"
            elif classification == DIGIFLAZZ_OUTCOME_PENDING:
                next_status, outcome = "PENDING_PROVIDER", "PENDING_PROVIDER"
            elif classification == DIGIFLAZZ_OUTCOME_DEFINITIVE_FAILURE:
                next_status, outcome = "FAILED", "FAILED"
            else:
                await db_execute_rowcount(
                    """
                    UPDATE topup
                    SET provider_last_check_at=CURRENT_TIMESTAMP,
                        provider_claim_id=NULL,
                        provider_claimed_at=NULL,
                        provider_claim_expires_at=NULL,
                        provider_payload=:provider_payload,
                        status_updated_at=CURRENT_TIMESTAMP
                    WHERE id=:id
                      AND provider_claim_id=:lease_id
                      AND provider_claim_expires_at > CURRENT_TIMESTAMP
                      AND payment_status='PAID'
                      AND COALESCE(topup_status, '') IN ('PROCESSING', 'PENDING_PROVIDER')
                      AND COALESCE(provider_outcome, '')='SENT_UNKNOWN'
                    """,
                    {
                        "id": order_id,
                        "lease_id": lease_id,
                        "provider_payload": values["provider_payload"],
                    },
                )
                continue
            updated = await db_execute_rowcount(
                """
                UPDATE topup
                SET topup_status=:topup_status,
                    provider_outcome=:provider_outcome,
                    sn=CASE WHEN COALESCE(:sn, '')='' THEN sn ELSE :sn END,
                    provider_last_error=:provider_error,
                    provider_rc=:provider_rc,
                    provider_price=:provider_price,
                    provider_selling_price=:provider_selling_price,
                    provider_last_balance=:provider_last_balance,
                    provider_last_check_at=CURRENT_TIMESTAMP,
                    provider_claim_id=NULL,
                    provider_claimed_at=NULL,
                    provider_claim_expires_at=NULL,
                    provider_payload=:provider_payload,
                    status_updated_at=CURRENT_TIMESTAMP
                WHERE id=:id
                  AND provider_claim_id=:lease_id
                  AND provider_claim_expires_at > CURRENT_TIMESTAMP
                  AND payment_status='PAID'
                  AND COALESCE(topup_status, '') IN ('PROCESSING', 'PENDING_PROVIDER')
                  AND COALESCE(provider_outcome, '')='SENT_UNKNOWN'
                """,
                {
                    "id": order_id,
                    "lease_id": lease_id,
                    "topup_status": next_status,
                    "provider_outcome": outcome,
                    "sn": data.get("sn") or "",
                    "provider_error": data.get("message") if next_status == "FAILED" else None,
                    **values,
                },
            )
            if updated:
                await _finish_provider_attempt(
                    str(order_id),
                    int((await db_query("SELECT MAX(attempt_number) FROM provider_attempts WHERE order_id=:id", {"id": order_id}) or [(1,)])[0][0] or 1),
                    request_state="RECONCILED",
                    local_outcome=outcome,
                    provider_status=status,
                    provider_transaction_id=str(data.get("trx_id") or data.get("transaction_id") or ""),
                    provider_response=response,
                    reconciled=True,
                )
                if outcome == "SUCCESS":
                    await finalize_order_promotions(str(order_id))
                elif outcome == "FAILED":
                    await release_order_promotions(str(order_id), include_redeemed=True)
        except Exception as exc:
            await release_provider_status_reconciliation(str(order_id), lease_id)
            logging.error(
                "Error rekonsiliasi provider order=%s error_type=%s",
                order_id,
                type(exc).__name__,
            )


async def polling_status_engine() -> None:
    await _reconcile_unknown_provider_orders()
    try:
        new_orders = await db_query(
            """
            SELECT id, COALESCE(target_id, phone), nominal, COALESCE(provider_retry_count, 0),
                   COALESCE(order_type, 'PREPAID'), COALESCE(product_cost, price, 0)
            FROM topup
            WHERE topup_status='PROCESSING'
              AND COALESCE(provider_outcome, 'NOT_SENT')='NOT_SENT'
            """
        )
    except Exception as exc:
        logging.error(f"ENGINE DB ERROR (new orders): {exc}")
        return

    runtime_settings = await get_provider_runtime_settings()
    testing_enabled = str(runtime_settings.get("digiflazz_testing_enabled") or "").lower() in {"1", "true", "yes", "on"}
    max_price_margin_percent = _float_value(runtime_settings.get("digiflazz_max_price_margin_percent"))
    callback_url = f"{settings.app_base_url.rstrip('/')}/api/webhook/digiflazz" if settings.app_base_url else None

    for order_id, phone, sku, retry_count, order_type, product_cost in new_orders:
        order_id = str(order_id)
        order_type = str(order_type or "PREPAID").upper()
        lease_id = await _claim_provider_order(order_id)
        if not lease_id:
            continue
        marked_sent_unknown = await db_execute_rowcount(
            """
            UPDATE topup
            SET provider_outcome='SENT_UNKNOWN', status_updated_at=CURRENT_TIMESTAMP
            WHERE id=:id
              AND provider_claim_id=:lease_id
              AND provider_claim_expires_at > CURRENT_TIMESTAMP
              AND payment_status='PAID'
              AND topup_status='PROCESSING'
              AND COALESCE(provider_outcome, 'NOT_SENT')='NOT_SENT'
            """,
            {"id": order_id, "lease_id": lease_id},
        )
        if not marked_sent_unknown:
            continue
        attempt_number = await _start_provider_attempt(order_id, sku, phone, order_type)
        try:
            if order_type == "POSTPAID":
                response = await pay_postpaid(sku, phone, order_id)
            else:
                max_price = int(_float_value(product_cost) * (1 + max_price_margin_percent / 100)) if product_cost and max_price_margin_percent >= 0 else None
                response = await kirim_digiflazz(sku, phone, order_id, max_price=max_price, callback_url=callback_url, testing=testing_enabled)
            data = digiflazz_response_data(response)
            status = _normalized_status(data.get("status"))
            classification = classify_digiflazz_transaction_response(response)
            values = _provider_payload_values(data)
            provider_id = str(data.get("trx_id") or data.get("transaction_id") or "")
            if classification == DIGIFLAZZ_OUTCOME_SENT_UNKNOWN:
                updated = await db_execute_rowcount(
                    """
                    UPDATE topup
                    SET provider_claim_id=NULL,
                        provider_claimed_at=NULL,
                        provider_claim_expires_at=NULL,
                        provider_last_check_at=CURRENT_TIMESTAMP,
                        provider_last_error=:error,
                        provider_payload=:provider_payload,
                        status_updated_at=CURRENT_TIMESTAMP
                    WHERE id=:id
                      AND provider_claim_id=:lease_id
                      AND provider_claim_expires_at > CURRENT_TIMESTAMP
                      AND payment_status='PAID'
                      AND topup_status='PROCESSING'
                      AND provider_outcome='SENT_UNKNOWN'
                    """,
                    {
                        "id": order_id,
                        "lease_id": lease_id,
                        "error": data.get("message") or "Provider outcome tidak diketahui",
                        "provider_payload": values["provider_payload"],
                    },
                )
                if updated:
                    await _finish_provider_attempt(
                        order_id,
                        attempt_number,
                        request_state="UNKNOWN",
                        local_outcome="SENT_UNKNOWN",
                        provider_status=status,
                        provider_transaction_id=provider_id,
                        provider_response=response,
                        error_type=str((response or {}).get("_error_type") or "UnknownProviderOutcome"),
                        error_message=str(data.get("message") or "Provider outcome tidak diketahui"),
                    )
                else:
                    await _discard_stale_provider_attempt(order_id, attempt_number)
                continue
            if classification == DIGIFLAZZ_OUTCOME_RETRYABLE_NOT_SENT:
                error_message = data.get("message") or "Request provider belum dikirim dan akan dicoba ulang"
                updated, exhausted = await _record_safe_provider_retry(
                    order_id,
                    lease_id,
                    _int_value(retry_count),
                    values,
                    str(error_message),
                )
                if updated:
                    await _finish_provider_attempt(
                        order_id,
                        attempt_number,
                        request_state="NOT_SENT",
                        local_outcome="FAILED" if exhausted else "RETRYABLE_NOT_SENT",
                        provider_status=status,
                        provider_transaction_id=provider_id,
                        provider_response=response,
                        error_type=str((response or {}).get("_error_type") or "SafePreSendFailure"),
                        error_message=str(error_message),
                    )
                    if exhausted:
                        await release_order_promotions(order_id, include_redeemed=True)
                else:
                    await _discard_stale_provider_attempt(order_id, attempt_number)
                continue
            if classification == DIGIFLAZZ_OUTCOME_SUCCESS:
                updated = await db_execute_rowcount("UPDATE topup SET topup_status='SUCCESS', provider_outcome='SUCCESS', sn=CASE WHEN COALESCE(:sn, '')='' THEN sn ELSE :sn END, provider_retry_count=0, provider_last_error=NULL, provider_rc=:provider_rc, provider_price=:provider_price, provider_selling_price=:provider_selling_price, provider_last_balance=:provider_last_balance, provider_last_check_at=CURRENT_TIMESTAMP, provider_claim_id=NULL, provider_claimed_at=NULL, provider_claim_expires_at=NULL, provider_payload=:provider_payload, status_updated_at=CURRENT_TIMESTAMP WHERE id=:id AND provider_claim_id=:lease_id AND provider_claim_expires_at > CURRENT_TIMESTAMP AND payment_status='PAID' AND topup_status='PROCESSING' AND provider_outcome='SENT_UNKNOWN'", {"id": order_id, "lease_id": lease_id, "sn": data.get("sn") or "", **values})
                if updated:
                    await _finish_provider_attempt(order_id, attempt_number, request_state="COMPLETED", local_outcome="SUCCESS", provider_status=status, provider_transaction_id=provider_id, provider_response=response)
                    await finalize_order_promotions(order_id)
                    phone_rows = await db_query("SELECT phone FROM topup WHERE id=:id", {"id": order_id})
                    if phone_rows and phone_rows[0][0]:
                        await db_execute(
                            """
                            INSERT INTO notification_outbox (
                                channel, recipient, subject, body, status,
                                reference_type, reference_id, event_key
                            )
                            VALUES ('WHATSAPP', :recipient, 'Topup sukses', :body, 'QUEUED', 'topup', :reference_id, :event_key)
                            ON CONFLICT(event_key) DO NOTHING
                            """,
                            {
                                "recipient": phone_rows[0][0],
                                "body": f"Order {order_id} sukses diproses provider.",
                                "reference_id": order_id,
                                "event_key": f"WHATSAPP:topup:{order_id}:Topup sukses",
                            },
                        )
                else:
                    await _discard_stale_provider_attempt(order_id, attempt_number)
                continue
            if classification == DIGIFLAZZ_OUTCOME_PENDING:
                updated = await db_execute_rowcount("UPDATE topup SET topup_status='PENDING_PROVIDER', provider_outcome='PENDING_PROVIDER', provider_retry_count=0, provider_last_error=NULL, provider_last_check_at=CURRENT_TIMESTAMP, provider_claim_id=NULL, provider_claimed_at=NULL, provider_claim_expires_at=NULL, provider_payload=:provider_payload, status_updated_at=CURRENT_TIMESTAMP WHERE id=:id AND provider_claim_id=:lease_id AND provider_claim_expires_at > CURRENT_TIMESTAMP AND payment_status='PAID' AND topup_status='PROCESSING' AND provider_outcome='SENT_UNKNOWN'", {"id": order_id, "lease_id": lease_id, **values})
                if updated:
                    await _finish_provider_attempt(order_id, attempt_number, request_state="COMPLETED", local_outcome="PENDING_PROVIDER", provider_status=status, provider_transaction_id=provider_id, provider_response=response)
                else:
                    await _discard_stale_provider_attempt(order_id, attempt_number)
                continue
            error_message = data.get("message") or "Provider menolak transaksi"
            updated = await db_execute_rowcount("UPDATE topup SET topup_status='FAILED', provider_outcome='FAILED', provider_last_error=:error, provider_rc=:provider_rc, provider_price=:provider_price, provider_selling_price=:provider_selling_price, provider_last_balance=:provider_last_balance, provider_last_check_at=CURRENT_TIMESTAMP, provider_claim_id=NULL, provider_claimed_at=NULL, provider_claim_expires_at=NULL, provider_payload=:provider_payload, status_updated_at=CURRENT_TIMESTAMP WHERE id=:id AND provider_claim_id=:lease_id AND provider_claim_expires_at > CURRENT_TIMESTAMP AND payment_status='PAID' AND topup_status='PROCESSING' AND provider_outcome='SENT_UNKNOWN'", {"id": order_id, "lease_id": lease_id, "error": error_message, **values})
            if updated:
                await _finish_provider_attempt(order_id, attempt_number, request_state="COMPLETED", local_outcome="FAILED", provider_status=status, provider_transaction_id=provider_id, provider_response=response, error_message=error_message)
                await release_order_promotions(order_id, include_redeemed=True)
            else:
                await _discard_stale_provider_attempt(order_id, attempt_number)
        except Exception as exc:
            error_type = type(exc).__name__
            safe_error = "Outcome provider tidak dapat dipastikan; rekonsiliasi diperlukan"
            updated = await db_execute_rowcount(
                """
                UPDATE topup
                SET provider_outcome='SENT_UNKNOWN',
                    provider_last_error=:error,
                    provider_claim_id=NULL,
                    provider_claimed_at=NULL,
                    provider_claim_expires_at=NULL,
                    status_updated_at=CURRENT_TIMESTAMP
                WHERE id=:id
                  AND provider_claim_id=:lease_id
                  AND provider_claim_expires_at > CURRENT_TIMESTAMP
                  AND payment_status='PAID'
                  AND topup_status='PROCESSING'
                  AND provider_outcome='SENT_UNKNOWN'
                """,
                {"id": order_id, "lease_id": lease_id, "error": safe_error},
            )
            if updated:
                await _finish_provider_attempt(order_id, attempt_number, request_state="UNKNOWN", local_outcome="SENT_UNKNOWN", error_type=error_type, error_message=safe_error)
            else:
                await _discard_stale_provider_attempt(order_id, attempt_number)
            logging.error("Unknown outcome kirim_digiflazz order=%s error_type=%s", order_id, error_type)

    try:
        pending_orders = await db_query("SELECT id, COALESCE(target_id, phone), nominal, COALESCE(order_type, 'PREPAID'), provider_last_check_at, created_at FROM topup WHERE topup_status='PENDING_PROVIDER'")
    except Exception as exc:
        logging.error(f"ENGINE DB ERROR (pending orders): {exc}")
        return
    for order_id, phone, sku, order_type, last_checked, created_at in pending_orders:
        if _recently_checked(last_checked):
            continue
        if not _can_reconcile_digiflazz_status(order_type, created_at):
            await db_execute(
                """
                UPDATE topup
                SET provider_last_check_at=CURRENT_TIMESTAMP,
                    provider_last_error='Cek status Digiflazz prabayar diblokir setelah 90 hari untuk mencegah transaksi baru',
                    status_updated_at=CURRENT_TIMESTAMP
                WHERE id=:id AND topup_status='PENDING_PROVIDER'
                """,
                {"id": order_id},
            )
            continue
        lease_id = await claim_provider_status_reconciliation(str(order_id))
        if not lease_id:
            continue
        try:
            response = await (cek_status_postpaid(sku, phone, order_id) if str(order_type or "PREPAID").upper() == "POSTPAID" else cek_status_digiflazz(sku, phone, order_id))
            data = digiflazz_response_data(response)
            values = _provider_payload_values(data)
            classification = classify_digiflazz_transaction_response(response)
            if classification == DIGIFLAZZ_OUTCOME_SUCCESS:
                updated = await db_execute_rowcount(
                    """
                    UPDATE topup
                    SET topup_status='SUCCESS',
                        provider_outcome='SUCCESS',
                        sn=CASE WHEN COALESCE(:sn, '')='' THEN sn ELSE :sn END,
                        provider_last_check_at=CURRENT_TIMESTAMP,
                        provider_claim_id=NULL,
                        provider_claimed_at=NULL,
                        provider_claim_expires_at=NULL,
                        provider_payload=:provider_payload,
                        status_updated_at=CURRENT_TIMESTAMP
                    WHERE id=:id
                      AND provider_claim_id=:lease_id
                      AND provider_claim_expires_at > CURRENT_TIMESTAMP
                      AND payment_status='PAID'
                      AND topup_status='PENDING_PROVIDER'
                      AND COALESCE(provider_outcome, 'NOT_SENT') IN ('NOT_SENT', 'SENT_UNKNOWN', 'PENDING_PROVIDER')
                    """,
                    {"id": order_id, "lease_id": lease_id, "sn": data.get("sn") or "", **values},
                )
                if updated:
                    await finalize_order_promotions(str(order_id))
            elif classification == DIGIFLAZZ_OUTCOME_DEFINITIVE_FAILURE:
                updated = await db_execute_rowcount(
                    """
                    UPDATE topup
                    SET topup_status='FAILED',
                        provider_outcome='FAILED',
                        provider_last_error=:error,
                        provider_last_check_at=CURRENT_TIMESTAMP,
                        provider_claim_id=NULL,
                        provider_claimed_at=NULL,
                        provider_claim_expires_at=NULL,
                        provider_payload=:provider_payload,
                        status_updated_at=CURRENT_TIMESTAMP
                    WHERE id=:id
                      AND provider_claim_id=:lease_id
                      AND provider_claim_expires_at > CURRENT_TIMESTAMP
                      AND payment_status='PAID'
                      AND topup_status='PENDING_PROVIDER'
                      AND COALESCE(provider_outcome, 'NOT_SENT') IN ('NOT_SENT', 'SENT_UNKNOWN', 'PENDING_PROVIDER')
                    """,
                    {
                        "id": order_id,
                        "lease_id": lease_id,
                        "error": data.get("message") or "Status gagal dari provider",
                        **values,
                    },
                )
                if updated:
                    await release_order_promotions(str(order_id), include_redeemed=True)
            else:
                await db_execute_rowcount(
                    """
                    UPDATE topup
                    SET provider_last_check_at=CURRENT_TIMESTAMP,
                        provider_claim_id=NULL,
                        provider_claimed_at=NULL,
                        provider_claim_expires_at=NULL,
                        provider_payload=:provider_payload,
                        status_updated_at=CURRENT_TIMESTAMP
                    WHERE id=:id
                      AND provider_claim_id=:lease_id
                      AND provider_claim_expires_at > CURRENT_TIMESTAMP
                      AND payment_status='PAID'
                      AND topup_status='PENDING_PROVIDER'
                      AND COALESCE(provider_outcome, 'NOT_SENT') IN ('NOT_SENT', 'SENT_UNKNOWN', 'PENDING_PROVIDER')
                    """,
                    {
                        "id": order_id,
                        "lease_id": lease_id,
                        "provider_payload": values["provider_payload"],
                    },
                )
        except Exception as exc:
            await release_provider_status_reconciliation(str(order_id), lease_id)
            logging.error(
                "Error cek_status order=%s error_type=%s",
                order_id,
                type(exc).__name__,
            )


async def reconcile_wallet_paid_orders() -> None:
    """Repair the narrow crash window after a wallet debit and before order promotion."""
    rows = await db_query(
        """
        SELECT t.id
        FROM topup t
        INNER JOIN wallet_ledger wl
            ON wl.reference_id=t.id
           AND wl.reference_type IN ('topup', 'postpaid')
           AND wl.entry_type='DEBIT'
        WHERE UPPER(COALESCE(t.payment_method, ''))='WALLET'
          AND COALESCE(t.payment_status, '')='UNPAID'
          AND COALESCE(t.topup_status, '')='PENDING_PAYMENT'
        """
    )
    for (order_id,) in rows:
        await db_execute(
            """
            UPDATE topup
            SET payment_status='PAID', topup_status='PROCESSING',
                status_updated_at=CURRENT_TIMESTAMP
            WHERE id=:id AND payment_status='UNPAID' AND topup_status='PENDING_PAYMENT'
            """,
            {"id": order_id},
        )


async def auto_engine_loop() -> None:
    if write_quiescence.enabled:
        write_quiescence.mark_engine_stopped()
        logging.warning("Embedded engine tidak dijalankan karena staging write quiescence aktif.")
        return

    while True:
        try:
            # One admission spans the whole cycle.  If a transition starts
            # while a cycle is active, this already-admitted work drains, then
            # the next admission is refused and the loop exits permanently.
            async with write_quiescence.writer_section("embedded_engine_cycle"):
                await reconcile_wallet_paid_orders()
                await reconcile_payment_engine()
                await reconcile_wallet_deposit_engine()
                await reconcile_open_payment_engine()
                await polling_status_engine()
                await provider_balance_watch_engine()
                await product_catalog_sync_engine()
        except WriteQuiescenceActive:
            write_quiescence.mark_engine_stopped()
            logging.warning("Embedded engine berhenti karena staging write quiescence aktif.")
            return
        except Exception as exc:
            logging.error(f"ENGINE ERROR {exc}")

        if write_quiescence.enabled:
            write_quiescence.mark_engine_stopped()
            logging.warning("Embedded engine selesai drain dan berhenti untuk write quiescence.")
            return

        await asyncio.sleep(settings.engine_poll_interval_seconds)
