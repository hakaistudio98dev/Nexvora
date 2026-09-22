import secrets
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, crypto, entitlements, netguard
from app.core.db import get_session
from app.core.deps import Principal, require
from app.core.errors import AppError
from app.models import Notification, NotificationChannel, NotificationDelivery, Tenant
from app.modules.notifications import service
from app.modules.notifications.service import EVENTS

router = APIRouter(prefix="/notifications", tags=["notifications"])


class ChannelIn(BaseModel):
    kind: str = Field(pattern="^(EMAIL|WEBHOOK)$")
    name: str = Field(min_length=2, max_length=100)
    target: str = Field(min_length=5, max_length=500, description="Alamat email atau URL https webhook")
    events: list[str] = Field(min_length=1)

    @field_validator("events")
    @classmethod
    def _ev(cls, v):  # noqa: ANN001, ANN206
        bad = [e for e in v if e not in EVENTS]
        if bad:
            raise ValueError(f"Event tidak dikenal: {', '.join(bad)}")
        return sorted(set(v))


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    events: list[str] | None = None
    is_active: bool | None = None
    rotate_secret: bool = False

    @field_validator("events")
    @classmethod
    def _ev(cls, v):  # noqa: ANN001, ANN206
        if v is not None and [e for e in v if e not in EVENTS]:
            raise ValueError("Event tidak dikenal")
        return v


def _n_out(n: Notification) -> dict:
    return {"id": str(n.id), "event_type": n.event_type, "severity": n.severity, "title": n.title, "body": n.body,
            "link": n.link, "data": n.data, "read": n.read_at is not None, "created_at": n.created_at}


async def _ch_out(s: AsyncSession, c: NotificationChannel) -> dict:
    stats = dict((await s.execute(select(NotificationDelivery.status, func.count()).where(
        NotificationDelivery.channel_id == c.id).group_by(NotificationDelivery.status))).all())
    return {"id": str(c.id), "kind": c.kind, "name": c.name, "target": c.target, "events": c.events,
            "is_active": c.is_active, "has_secret": bool(c.secret_enc), "created_at": c.created_at,
            "deliveries": {"sent": stats.get("SENT", 0), "pending": stats.get("PENDING", 0), "failed": stats.get("FAILED", 0)}}


@router.get("/events")
async def events(p: Principal = Depends(require("notification:read"))):
    return [{"code": k, "label": v[0], "severity": v[1], "in_app": v[2]} for k, v in EVENTS.items()]


@router.get("")
async def inbox(unread: bool = False, limit: int = Query(50, ge=1, le=200), before: datetime | None = None,
                p: Principal = Depends(require("notification:read")), s: AsyncSession = Depends(get_session)):
    stmt = (select(Notification).where(Notification.tenant_id == p.tenant_id, Notification.in_app.is_(True))
            .order_by(Notification.created_at.desc()).limit(limit))
    if unread:
        stmt = stmt.where(Notification.read_at.is_(None))
    if before:
        stmt = stmt.where(Notification.created_at < before)
    count = await s.scalar(select(func.count()).select_from(Notification).where(
        Notification.tenant_id == p.tenant_id, Notification.in_app.is_(True), Notification.read_at.is_(None)))
    return {"unread": count or 0, "items": [_n_out(n) for n in (await s.scalars(stmt)).all()]}


@router.post("/{notification_id}/read")
async def mark_read(notification_id: UUID, p: Principal = Depends(require("notification:read")),
                    s: AsyncSession = Depends(get_session)):
    await s.execute(update(Notification).where(Notification.id == notification_id, Notification.tenant_id == p.tenant_id,
                                               Notification.read_at.is_(None)).values(read_at=service.now()))
    return {"ok": True}


@router.post("/read-all")
async def read_all(p: Principal = Depends(require("notification:read")), s: AsyncSession = Depends(get_session)):
    r = await s.execute(update(Notification).where(Notification.tenant_id == p.tenant_id, Notification.read_at.is_(None))
                        .values(read_at=service.now()))
    return {"marked": r.rowcount}


# ------------------------------------------------------------------ kanal
@router.get("/channels")
async def list_channels(p: Principal = Depends(require("notification:manage")), s: AsyncSession = Depends(get_session)):
    rows = (await s.scalars(select(NotificationChannel).where(NotificationChannel.tenant_id == p.tenant_id)
                            .order_by(NotificationChannel.created_at))).all()
    return [await _ch_out(s, c) for c in rows]


@router.post("/channels", status_code=201)
async def create_channel(body: ChannelIn, p: Principal = Depends(require("notification:manage")),
                         s: AsyncSession = Depends(get_session)):
    secret = None
    if body.kind == "WEBHOOK":
        entitlements.require_feature(p, "webhooks")
        netguard.validate_public_url(body.target)
        secret = "whsec_" + secrets.token_urlsafe(32)
    else:
        try:
            from pydantic import TypeAdapter  # noqa: PLC0415
            body.target = str(TypeAdapter(EmailStr).validate_python(body.target)).lower()
        except Exception:  # noqa: BLE001
            raise AppError(422, "INVALID_EMAIL", "Alamat email tidak valid") from None
    c = NotificationChannel(tenant_id=p.tenant_id, kind=body.kind, name=body.name, target=body.target,
                            events=body.events, secret_enc=crypto.encrypt(secret) if secret else None)
    s.add(c)
    await s.flush()
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="notification_channel.created",
                       entity_type="notification_channel", entity_id=c.id,
                       after={"kind": c.kind, "target": c.target, "events": c.events}, correlation_id=p.correlation_id, ip=p.ip)
    out = await _ch_out(s, c)
    if secret:
        out["secret"] = secret  # ditampilkan SEKALI untuk verifikasi tanda tangan di sisi penerima
    return out


async def _channel(s: AsyncSession, p: Principal, cid: UUID) -> NotificationChannel:
    c = await s.scalar(select(NotificationChannel).where(NotificationChannel.id == cid,
                                                         NotificationChannel.tenant_id == p.tenant_id).with_for_update())
    if c is None:
        raise AppError(404, "NOT_FOUND", "Kanal tidak ditemukan")
    return c


@router.patch("/channels/{channel_id}")
async def update_channel(channel_id: UUID, body: ChannelUpdate, p: Principal = Depends(require("notification:manage")),
                         s: AsyncSession = Depends(get_session)):
    c = await _channel(s, p, channel_id)
    if body.name is not None:
        c.name = body.name
    if body.events is not None:
        c.events = sorted(set(body.events))
    if body.is_active is not None:
        c.is_active = body.is_active
    out_secret = None
    if body.rotate_secret and c.kind == "WEBHOOK":
        out_secret = "whsec_" + secrets.token_urlsafe(32)
        c.secret_enc = crypto.encrypt(out_secret)
    await s.flush()
    out = await _ch_out(s, c)
    if out_secret:
        out["secret"] = out_secret
    return out


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: UUID, p: Principal = Depends(require("notification:manage")),
                       s: AsyncSession = Depends(get_session)):
    c = await _channel(s, p, channel_id)
    n = Notification(id=uuid4(), tenant_id=p.tenant_id, event_type="TEST", severity="INFO",
                     title="Tes notifikasi Nexvora", body="Jika pesan ini sampai, kanal sudah benar.",
                     data={"test": True}, link="/settings", in_app=False, created_at=service.now())
    try:
        tenant = await s.get(Tenant, p.tenant_id)
        code = await (service.send_webhook(c, n, n.id, tenant.slug) if c.kind == "WEBHOOK" else service.send_email(c, n))
        return {"ok": True, "response_code": code}
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        raise AppError(502, "TEST_FAILED", f"Gagal mengirim: {str(e)[:200] or e.__class__.__name__}") from None


@router.get("/channels/{channel_id}/deliveries")
async def deliveries(channel_id: UUID, p: Principal = Depends(require("notification:manage")),
                     s: AsyncSession = Depends(get_session)):
    c = await _channel(s, p, channel_id)
    rows = (await s.execute(select(NotificationDelivery, Notification.title, Notification.event_type)
                            .join(Notification, Notification.id == NotificationDelivery.notification_id)
                            .where(NotificationDelivery.channel_id == c.id)
                            .order_by(NotificationDelivery.created_at.desc()).limit(50))).all()
    return [{"id": str(d.id), "event_type": ev, "title": t, "status": d.status, "attempts": d.attempts,
             "last_error": d.last_error, "response_code": d.response_code, "created_at": d.created_at,
             "sent_at": d.sent_at, "next_attempt_at": d.next_attempt_at} for d, t, ev in rows]
