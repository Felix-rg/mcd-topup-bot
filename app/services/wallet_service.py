import json
from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy import text

from app.core.database import get_engine
from app.core.write_quiescence import write_quiescence
from app.database import db_execute, db_query, init_db
from app.services.tripay_service import parse_tripay_fee


def _float_value(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _money_value(value: Any) -> int:
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _transaction_reference(transaction: Dict[str, Any]) -> str:
    for key in ("reference", "trx_id", "transaction_id", "id"):
        value = str(transaction.get(key) or "").strip()
        if value:
            return value
    return ""


def _transaction_fee(transaction: Dict[str, Any]) -> int:
    parsed = parse_tripay_fee(transaction, amount=transaction.get("amount"))
    return parsed.total_fee if parsed.valid else 0


async def wallet_balance(customer_id: int) -> float:
    # This nominal read lazily creates the account row, so it is a writer.
    async with write_quiescence.writer_section("wallet_balance"):
        return await _wallet_balance(customer_id)


async def _wallet_balance(customer_id: int) -> float:
    await db_execute(
        """
        INSERT INTO wallet_accounts (customer_id, balance)
        VALUES (:customer_id, 0)
        ON CONFLICT(customer_id) DO NOTHING
        """,
        {"customer_id": customer_id},
    )
    rows = await db_query("SELECT balance FROM wallet_accounts WHERE customer_id=:customer_id", {"customer_id": customer_id})
    return _money_value(rows[0][0]) if rows else 0


async def wallet_add_entry(
    *,
    customer_id: int,
    entry_type: str,
    amount: float,
    reference_type: str,
    reference_id: str,
    note: str,
    idempotency_key: Optional[str] = None,
    actor: Optional[str] = None,
) -> float:
    async with write_quiescence.writer_section("wallet_add_entry"):
        return await _wallet_add_entry(
            customer_id=customer_id,
            entry_type=entry_type,
            amount=amount,
            reference_type=reference_type,
            reference_id=reference_id,
            note=note,
            idempotency_key=idempotency_key,
            actor=actor,
        )


async def _wallet_add_entry(
    *,
    customer_id: int,
    entry_type: str,
    amount: float,
    reference_type: str,
    reference_id: str,
    note: str,
    idempotency_key: Optional[str] = None,
    actor: Optional[str] = None,
) -> float:
    normalized_type = (entry_type or "CREDIT").upper()
    if normalized_type not in {"CREDIT", "DEBIT"}:
        raise HTTPException(status_code=400, detail="Tipe wallet harus CREDIT atau DEBIT")

    amount_value = _money_value(amount)
    if amount_value <= 0:
        raise HTTPException(status_code=400, detail="Nominal wallet harus lebih dari 0")

    clean_reference_type = str(reference_type or "wallet").strip() or "wallet"
    clean_reference_id = str(reference_id or "").strip()
    if not clean_reference_id:
        raise HTTPException(status_code=400, detail="Reference wallet wajib diisi")

    clean_idempotency_key = (
        str(idempotency_key or "").strip()
        or f"wallet:{customer_id}:{normalized_type}:{clean_reference_type}:{clean_reference_id}"
    )

    await init_db()
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO wallet_accounts (customer_id, balance)
                VALUES (:customer_id, 0)
                ON CONFLICT(customer_id) DO NOTHING
                """
            ),
            {"customer_id": customer_id},
        )
        claim = await conn.execute(
            text(
                """
                INSERT INTO idempotency_keys (key, scope, reference_type, reference_id)
                VALUES (:key, 'wallet_ledger', :reference_type, :reference_id)
                ON CONFLICT(key) DO NOTHING
                """
            ),
            {
                "key": clean_idempotency_key,
                "reference_type": clean_reference_type,
                "reference_id": clean_reference_id,
            },
        )
        if claim.rowcount == 0:
            existing = await conn.execute(
                text(
                    """
                    SELECT balance_after
                    FROM wallet_ledger
                    WHERE idempotency_key=:idempotency_key
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {"idempotency_key": clean_idempotency_key},
            )
            row = existing.first()
            if row is not None:
                return _money_value(row[0])
            current = await conn.execute(
                text("SELECT balance FROM wallet_accounts WHERE customer_id=:customer_id"),
                {"customer_id": customer_id},
            )
            row = current.first()
            return _money_value(row[0]) if row else 0

        if normalized_type == "DEBIT":
            updated = await conn.execute(
                text(
                    """
                    UPDATE wallet_accounts
                    SET balance=balance - :amount,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE customer_id=:customer_id
                      AND balance >= :amount
                    """
                ),
                {"customer_id": customer_id, "amount": amount_value},
            )
            if updated.rowcount != 1:
                raise HTTPException(status_code=400, detail="Saldo wallet tidak cukup")
        else:
            updated = await conn.execute(
                text(
                    """
                    UPDATE wallet_accounts
                    SET balance=balance + :amount,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE customer_id=:customer_id
                    """
                ),
                {"customer_id": customer_id, "amount": amount_value},
            )
            if updated.rowcount != 1:
                raise HTTPException(status_code=500, detail="Gagal memperbarui saldo wallet")

        balance_result = await conn.execute(
            text("SELECT balance FROM wallet_accounts WHERE customer_id=:customer_id"),
            {"customer_id": customer_id},
        )
        balance_row = balance_result.first()
        next_balance = _money_value(balance_row[0]) if balance_row else 0

        await conn.execute(
            text(
                """
                INSERT INTO wallet_ledger (
                    customer_id, entry_type, amount, balance_after,
                    reference_type, reference_id, idempotency_key, actor, note
                )
                VALUES (
                    :customer_id, :entry_type, :amount, :balance_after,
                    :reference_type, :reference_id, :idempotency_key, :actor, :note
                )
                """
            ),
            {
                "customer_id": customer_id,
                "entry_type": normalized_type,
                "amount": amount_value,
                "balance_after": next_balance,
                "reference_type": clean_reference_type,
                "reference_id": clean_reference_id,
                "idempotency_key": clean_idempotency_key,
                "actor": actor,
                "note": note,
            },
        )
        return next_balance


async def credit_wallet_deposit_if_new(customer_id: int, transaction: Dict[str, Any]) -> bool:
    async with write_quiescence.writer_section("wallet_deposit_credit"):
        return await _credit_wallet_deposit_if_new(customer_id, transaction)


async def _credit_wallet_deposit_if_new(customer_id: int, transaction: Dict[str, Any]) -> bool:
    reference = _transaction_reference(transaction)
    if not reference:
        return False

    merchant_ref = str(transaction.get("merchant_ref") or "").strip()
    existing = await db_query(
        """
        SELECT id, customer_id, amount, fee, status
        FROM customer_wallet_deposits
        WHERE reference=:reference
           OR (:merchant_ref != '' AND merchant_ref=:merchant_ref)
        LIMIT 1
        """,
        {"reference": reference, "merchant_ref": merchant_ref},
    )
    if existing and str(existing[0][4] or "").upper() == "CREDITED":
        return False

    amount = (
        (_float_value(existing[0][2]) if existing else 0)
        or _float_value(transaction.get("amount_received"))
        or _float_value(transaction.get("total_amount"))
        or _float_value(transaction.get("amount"))
    )
    total_fee = (_float_value(existing[0][3]) if existing else 0) or _transaction_fee(transaction)
    if amount <= 0:
        return False

    payload = _json_dumps(transaction)
    if existing:
        await db_execute(
            """
            UPDATE customer_wallet_deposits
            SET reference=:reference,
                merchant_ref=COALESCE(NULLIF(merchant_ref, ''), :merchant_ref),
                amount=:amount,
                fee=:fee,
                payload=:payload,
                last_check_at=CURRENT_TIMESTAMP
            WHERE id=:id
            """,
            {
                "id": existing[0][0],
                "reference": reference,
                "merchant_ref": merchant_ref,
                "amount": amount,
                "fee": total_fee,
                "payload": payload,
            },
        )
    else:
        await db_execute(
            """
            INSERT INTO customer_wallet_deposits (
                customer_id, provider, reference, merchant_ref, amount, fee, status, payload, last_check_at
            )
            VALUES (
                :customer_id, 'tripay', :reference, :merchant_ref, :amount, :fee, 'PENDING',
                :payload, CURRENT_TIMESTAMP
            )
            """,
            {
                "customer_id": customer_id,
                "reference": reference,
                "merchant_ref": merchant_ref,
                "amount": amount,
                "fee": total_fee,
                "payload": payload,
            },
        )

    await wallet_add_entry(
        customer_id=customer_id,
        entry_type="CREDIT",
        amount=amount,
        reference_type="wallet_deposit",
        reference_id=reference,
        note=f"Deposit wallet Tripay {reference}",
    )
    await db_execute(
        """
        UPDATE customer_wallet_deposits
        SET status='CREDITED',
            credited_at=CURRENT_TIMESTAMP,
            last_check_at=CURRENT_TIMESTAMP
        WHERE reference=:reference
        """,
        {"reference": reference},
    )
    return True
