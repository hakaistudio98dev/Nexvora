"""Adapter Midtrans Snap (VA, QRIS, e-wallet, kartu). Aktif bila BILLING_PROVIDER=midtrans."""
import base64
import hashlib
import hmac

import httpx

from app.core.config import get_settings
from app.core.errors import AppError
from app.models import Invoice


def _base() -> str:
    return ("https://app.midtrans.com" if get_settings().midtrans_is_production
            else "https://app.sandbox.midtrans.com")


async def create_payment(inv: Invoice, *, plan_name: str, email: str, name: str) -> str:
    s = get_settings()
    if not s.midtrans_server_key:
        raise AppError(503, "PAYMENT_NOT_CONFIGURED", "Payment gateway belum dikonfigurasi")
    auth = base64.b64encode(f"{s.midtrans_server_key}:".encode()).decode()
    amount = int(inv.amount)
    body = {
        "transaction_details": {"order_id": str(inv.id), "gross_amount": amount},
        "item_details": [{"id": inv.plan_code, "price": amount, "quantity": 1,
                          "name": f"{plan_name} {inv.billing_cycle.lower()} {inv.number}"[:50]}],
        "customer_details": {"first_name": name[:50], "email": email},
        "callbacks": {"finish": f"{s.public_app_url.rstrip('/')}/billing?invoice={inv.id}"},
    }
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{_base()}/snap/v1/transactions", json=body,
                             headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
    except httpx.HTTPError:
        raise AppError(502, "PAYMENT_GATEWAY_DOWN", "Payment gateway sedang tidak bisa dihubungi, coba lagi") from None
    if r.status_code >= 300:
        raise AppError(502, "PAYMENT_GATEWAY_ERROR", "Payment gateway menolak permintaan pembayaran")
    return r.json()["redirect_url"]


def verify_signature(payload: dict) -> bool:
    key = get_settings().midtrans_server_key
    if not key:
        return False
    raw = f"{payload.get('order_id', '')}{payload.get('status_code', '')}{payload.get('gross_amount', '')}{key}"
    return hmac.compare_digest(hashlib.sha512(raw.encode()).hexdigest(), str(payload.get("signature_key", "")))


def outcome(payload: dict) -> str | None:
    """PAID / FAILED / None (masih menunggu)."""
    st = payload.get("transaction_status")
    if st == "settlement" or (st == "capture" and payload.get("fraud_status", "accept") == "accept"):
        return "PAID"
    if st in ("expire", "cancel", "deny", "failure"):
        return "FAILED"
    return None
