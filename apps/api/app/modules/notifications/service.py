"""Notifikasi: in-app, email (SMTP), dan webhook bertanda tangan HMAC.

Event dideteksi oleh worker (scan berkala) sehingga modul order/gudang/pengiriman tidak perlu diubah,
dan setiap event punya dedup_key — satu kejadian hanya menghasilkan satu notifikasi.
"""
import asyncio
import hashlib
import hmac
import json
import smtplib
import time
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from uuid import UUID

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto, netguard
from app.core.config import get_settings
from app.core.errors import AppError
from app.models import Notification, NotificationChannel, NotificationDelivery, Tenant

EVENTS: dict[str, tuple[str, str, bool]] = {
    # kode: (label, severity default, tampil di inbox)
    "LOW_STOCK": ("Stok menipis", "WARNING", True),
    "ORDER_ON_HOLD": ("Order tertahan karena stok kurang", "WARNING", True),
    "SLA_RISK": ("Order mendekati batas SLA", "WARNING", True),
    "SLA_BREACH": ("Order melewati batas SLA", "CRITICAL", True),
    "SHIPMENT_PROBLEM": ("Masalah pengiriman", "CRITICAL", True),
    "WMS_EXCEPTION": ("Exception gudang", "WARNING", True),
    "RETURN_REQUESTED": ("Retur baru", "INFO", True),
    "SUBSCRIPTION": ("Langganan", "WARNING", True),
    "ORDER_STATUS": ("Status order berubah (untuk integrasi)", "INFO", False),
}
BACKOFF = [60, 300, 1800, 7200, 21600]
_WEBHOOK_TRANSPORT: httpx.AsyncBaseTransport | None = None  # dipakai tes


def now() -> datetime:
    return datetime.now(UTC)


async def notify(s: AsyncSession, tenant_id: UUID, event: str, title: str, body: str = "", *, data: dict | None = None,
                 link: str | None = None, dedup_key: str | None = None, severity: str | None = None) -> UUID | None:
    label, sev, in_app = EVENTS[event]
    channels = (await s.scalars(select(NotificationChannel).where(
        NotificationChannel.tenant_id == tenant_id, NotificationChannel.is_active.is_(True),
        NotificationChannel.events.contains([event])))).all()
    if not in_app and not channels:
        return None  # event khusus integrasi & tidak ada yang berlangganan → tidak perlu disimpan
    nid = await s.scalar(
        pg_insert(Notification).values(tenant_id=tenant_id, event_type=event, severity=severity or sev, title=title[:200],
                                       body=body, data=data or {}, link=link, in_app=in_app, dedup_key=dedup_key)
        .on_conflict_do_nothing(index_elements=["tenant_id", "dedup_key"], index_where=text("dedup_key IS NOT NULL"))
        .returning(Notification.id))
    if nid is None:
        return None
    for ch in channels:
        s.add(NotificationDelivery(tenant_id=tenant_id, notification_id=nid, channel_id=ch.id))
    await s.flush()
    return nid


# ------------------------------------------------------------------ deteksi event (worker, konteks superadmin)
async def scan_all(s: AsyncSession) -> int:
    n = 0
    q = lambda sql, **kw: s.execute(text(sql), kw)  # noqa: E731

    # Stok menipis: sekali per SKU per gudang per hari
    for r in (await q("""
        SELECT b.tenant_id, b.warehouse_id, b.sku_id, b.available, k.sku_code, w.code wh,
               COALESCE(k.reorder_point, ts.low_stock_threshold, 5) thr
        FROM inventory_balances b JOIN skus k ON k.id = b.sku_id JOIN warehouses w ON w.id = b.warehouse_id
        LEFT JOIN tenant_settings ts ON ts.tenant_id = b.tenant_id
        WHERE k.is_active AND w.is_active AND b.available <= COALESCE(k.reorder_point, ts.low_stock_threshold, 5)
          AND EXISTS (SELECT 1 FROM inventory_ledger l WHERE l.warehouse_id = b.warehouse_id AND l.sku_id = b.sku_id)
    """)).all():
        if await notify(s, r.tenant_id, "LOW_STOCK", f"Stok {r.sku_code} tinggal {r.available} di {r.wh}",
                        f"Batas stok menipis {r.thr}. Segera pesan ulang atau terima stok.", link="/inventory",
                        data={"sku_code": r.sku_code, "warehouse": r.wh, "available": r.available},
                        dedup_key=f"low:{r.warehouse_id}:{r.sku_id}:{now():%Y%m%d}",
                        severity="CRITICAL" if r.available <= 0 else None):
            n += 1

    # Order tertahan karena stok kurang
    for r in (await q("""
        SELECT id, tenant_id, order_number FROM orders
        WHERE stock_status = 'OUT_OF_STOCK' AND status IN ('CREATED','PAID') AND updated_at > now() - interval '3 days'
    """)).all():
        if await notify(s, r.tenant_id, "ORDER_ON_HOLD", f"{r.order_number} tertahan: stok belum cukup",
                        "Terima stok lalu coba reservasi ulang dari halaman Order.", link="/orders",
                        data={"order_number": r.order_number}, dedup_key=f"hold:{r.id}"):
            n += 1

    # SLA: dari dibayar sampai diserahkan ke kurir
    for r in (await q("""
        SELECT o.id, o.tenant_id, o.order_number, o.status,
               o.paid_at + make_interval(hours => COALESCE(ts.sla_ship_hours, 24)) due_at,
               COALESCE(ts.sla_risk_hours, 4) risk
        FROM orders o LEFT JOIN tenant_settings ts ON ts.tenant_id = o.tenant_id
        WHERE o.status IN ('PAID','ALLOCATED','PICKING','PACKING','READY_TO_SHIP') AND o.paid_at IS NOT NULL
    """)).all():
        t = now()
        if t > r.due_at:
            if await notify(s, r.tenant_id, "SLA_BREACH", f"{r.order_number} melewati SLA",
                            f"Seharusnya diserahkan ke kurir sebelum {r.due_at:%d %b %H:%M} UTC; status sekarang {r.status}.",
                            link="/analytics", data={"order_number": r.order_number, "status": r.status},
                            dedup_key=f"sla_breach:{r.id}"):
                n += 1
        elif r.risk and r.due_at - t <= timedelta(hours=r.risk):
            if await notify(s, r.tenant_id, "SLA_RISK", f"{r.order_number} harus dikirim dalam {max(1, int((r.due_at - t).total_seconds() // 3600))} jam",
                            f"Status sekarang {r.status}.", link="/analytics",
                            data={"order_number": r.order_number, "status": r.status}, dedup_key=f"sla_risk:{r.id}"):
                n += 1

    # Masalah pengiriman
    for r in (await q("""
        SELECT s.id, s.tenant_id, s.status, s.tracking_number, o.order_number FROM shipments s JOIN orders o ON o.id = s.order_id
        WHERE s.status IN ('FAILED_DELIVERY','RETURNED_TO_SENDER') AND s.updated_at > now() - interval '3 days'
    """)).all():
        label = "gagal diantar" if r.status == "FAILED_DELIVERY" else "dikembalikan kurir"
        if await notify(s, r.tenant_id, "SHIPMENT_PROBLEM", f"Paket {r.order_number} {label}",
                        f"Resi {r.tracking_number}. Hubungi pelanggan atau kurir.", link="/shipping",
                        data={"order_number": r.order_number, "tracking_number": r.tracking_number, "status": r.status},
                        dedup_key=f"ship:{r.id}:{r.status}"):
            n += 1

    # Exception gudang baru
    for r in (await q("""
        SELECT e.id, e.tenant_id, e.exc_type, e.note, e.quantity FROM wms_exceptions e
        WHERE e.status = 'OPEN' AND e.created_at > now() - interval '2 days'
    """)).all():
        if await notify(s, r.tenant_id, "WMS_EXCEPTION", f"Exception gudang: {r.exc_type.replace('_', ' ').lower()}",
                        r.note or "", link="/wms", data={"type": r.exc_type, "quantity": r.quantity},
                        dedup_key=f"exc:{r.id}"):
            n += 1

    # Retur baru
    for r in (await q("""
        SELECT r.id, r.tenant_id, r.number, r.reason_code, o.order_number FROM returns r JOIN orders o ON o.id = r.order_id
        WHERE r.status IN ('REQUESTED','APPROVED') AND r.created_at > now() - interval '2 days'
    """)).all():
        if await notify(s, r.tenant_id, "RETURN_REQUESTED", f"Retur {r.number} untuk {r.order_number}",
                        f"Alasan: {r.reason_code.replace('_', ' ').lower()}", link="/returns",
                        data={"number": r.number, "order_number": r.order_number}, dedup_key=f"ret:{r.id}"):
            n += 1

    # Langganan: trial hampir habis / belum dibayar
    for r in (await q("""
        SELECT tenant_id, status, trial_ends_at, grace_until FROM subscriptions
        WHERE (status = 'TRIALING' AND trial_ends_at < now() + interval '3 days') OR status = 'PAST_DUE'
    """)).all():
        if r.status == "TRIALING":
            title, key = "Masa coba segera berakhir", f"sub:trial:{r.tenant_id}:{now():%Y%m%d}"
        else:
            title, key = "Pembayaran langganan belum diterima", f"sub:pastdue:{r.tenant_id}:{now():%Y%m%d}"
        if await notify(s, r.tenant_id, "SUBSCRIPTION", title, "Buka menu Langganan untuk melanjutkan.",
                        link="/billing", dedup_key=key):
            n += 1

    # Perubahan status order → hanya untuk kanal webhook yang berlangganan ORDER_STATUS
    for r in (await q("""
        SELECT h.id, h.tenant_id, h.to_status, h.from_status, h.reason, o.order_number, o.external_ref, o.channel
        FROM order_status_history h JOIN orders o ON o.id = h.order_id
        WHERE h.created_at > now() - interval '15 minutes'
          AND EXISTS (SELECT 1 FROM notification_channels c WHERE c.tenant_id = h.tenant_id AND c.is_active
                      AND c.events ? 'ORDER_STATUS')
        ORDER BY h.id
    """)).all():
        if await notify(s, r.tenant_id, "ORDER_STATUS", f"{r.order_number}: {r.to_status}", r.reason or "",
                        data={"order_number": r.order_number, "external_ref": r.external_ref, "channel": r.channel,
                              "from_status": r.from_status, "to_status": r.to_status},
                        dedup_key=f"os:{r.id}"):
            n += 1
    return n


# ------------------------------------------------------------------ pengiriman
def sign(secret: str, ts: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def payload(n: Notification, tenant_slug: str) -> dict:
    return {"id": str(n.id), "event": n.event_type, "severity": n.severity, "title": n.title, "body": n.body,
            "data": n.data, "link": n.link, "tenant": tenant_slug, "created_at": n.created_at.isoformat()}


async def send_webhook(ch: NotificationChannel, n: Notification, delivery_id: UUID, tenant_slug: str) -> int:
    netguard.validate_public_url(ch.target)  # cek ulang saat kirim (DNS bisa berubah)
    body = json.dumps(payload(n, tenant_slug), separators=(",", ":")).encode()
    ts = int(time.time())
    headers = {"Content-Type": "application/json", "User-Agent": "Nexvora-Webhook/1.0", "X-Nexvora-Event": n.event_type,
               "X-Nexvora-Delivery": str(delivery_id)}
    secret = crypto.decrypt(ch.secret_enc)
    if secret:
        headers["X-Nexvora-Signature"] = f"t={ts},v1={sign(secret, ts, body)}"
    async with httpx.AsyncClient(timeout=10, transport=_WEBHOOK_TRANSPORT, follow_redirects=False) as c:
        r = await c.post(ch.target, content=body, headers=headers)
    if r.status_code >= 300:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.status_code


def _smtp_send(to: str, subject: str, text_body: str) -> None:
    st = get_settings()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = st.smtp_from, to, subject
    msg.set_content(text_body)
    with smtplib.SMTP(st.smtp_host, st.smtp_port, timeout=15) as smtp:
        if st.smtp_starttls:
            smtp.starttls()
        if st.smtp_user:
            smtp.login(st.smtp_user, st.smtp_password or "")
        smtp.send_message(msg)


async def send_email(ch: NotificationChannel, n: Notification) -> int:
    if not get_settings().smtp_host:
        raise PermissionError("SMTP belum dikonfigurasi di server (SMTP_HOST)")
    base = get_settings().public_app_url.rstrip("/")
    text_body = f"{n.title}\n\n{n.body}\n\n{base}{n.link or ''}\n\n— Nexvora"
    await asyncio.to_thread(_smtp_send, ch.target, f"[{n.severity}] {n.title}", text_body)
    return 250


async def deliver_due(s: AsyncSession, limit: int = 50) -> dict:
    rows = (await s.scalars(select(NotificationDelivery).where(
        NotificationDelivery.status == "PENDING", NotificationDelivery.next_attempt_at <= now())
        .order_by(NotificationDelivery.next_attempt_at).limit(limit).with_for_update(skip_locked=True))).all()
    sent = failed = 0
    for d in rows:
        ch = await s.get(NotificationChannel, d.channel_id)
        n = await s.get(Notification, d.notification_id)
        tenant = await s.get(Tenant, d.tenant_id)
        d.attempts += 1
        try:
            if not ch.is_active:
                raise PermissionError("Kanal dinonaktifkan")
            code = await (send_webhook(ch, n, d.id, tenant.slug) if ch.kind == "WEBHOOK" else send_email(ch, n))
            d.status, d.response_code, d.sent_at, d.last_error = "SENT", code, now(), None
            sent += 1
        except (PermissionError, AppError) as e:  # kesalahan konfigurasi: tidak dicoba ulang
            d.status, d.last_error = "FAILED", str(getattr(e, "message", e))[:500]
            failed += 1
        except Exception as e:  # noqa: BLE001  jaringan / tujuan error → coba lagi dengan jeda bertahap
            d.last_error = str(e)[:500] or e.__class__.__name__
            if d.attempts > len(BACKOFF):
                d.status = "FAILED"
                failed += 1
            else:
                d.next_attempt_at = now() + timedelta(seconds=BACKOFF[d.attempts - 1])
    await s.flush()
    return {"sent": sent, "failed": failed}
