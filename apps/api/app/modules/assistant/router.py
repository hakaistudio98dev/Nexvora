

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import Principal, require
from app.core.entitlements import check_permission
from app.core.errors import AppError
from app.modules.assistant import engine_llm, engine_rules
from app.modules.assistant import tools as toolkit

router = APIRouter(prefix="/assistant", tags=["assistant"])

HELP = ("Saya bisa membantu soal order, stok, pengiriman, retur, dan laporan. Contoh: “berapa order hari ini”, "
        "“stok TSH-BLK-M”, “order mana yang terlambat”, “lacak SO-2609-000001”, atau “mulai picking SO-2609-000001”.")


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class ActIn(BaseModel):
    name: str = Field(max_length=40)
    args: dict = Field(default_factory=dict)


def engine_name() -> str:
    st = get_settings()
    if st.assistant_engine == "llm" or (st.assistant_engine == "auto" and st.anthropic_api_key):
        return "llm"
    return "rules"


async def _save(s: AsyncSession, p: Principal, role: str, content: str, tool_calls: list | None = None,
                engine: str | None = None) -> None:
    await s.execute(text("""
        INSERT INTO assistant_messages(tenant_id, user_id, role, content, tool_calls, engine)
        VALUES (:t, :u, :r, :c, CAST(:tc AS jsonb), :e)"""),
        {"t": p.tenant_id, "u": p.user_id, "r": role, "c": content[:4000],
         "tc": __import__("json").dumps(tool_calls or []), "e": engine})


@router.get("/capabilities")
async def capabilities(p: Principal = Depends(require("assistant:use"))):
    """Perkakas yang tersedia untuk peran & paket pengguna ini, beserta contoh kalimatnya."""
    items = [{"name": t.name, "description": t.description, "writes": t.writes, "examples": t.examples}
             for t in toolkit.available(p)]
    return {"engine": engine_name(), "tools": items,
            "suggestions": [e for t in toolkit.available(p) if not t.writes for e in t.examples][:8]}


@router.get("/history")
async def history(limit: int = Query(30, ge=1, le=100), p: Principal = Depends(require("assistant:use")),
                  s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(text("""
        SELECT role, content, created_at FROM assistant_messages
        WHERE tenant_id = :t AND user_id = :u ORDER BY created_at DESC, id DESC LIMIT :l"""),
        {"t": p.tenant_id, "u": p.user_id, "l": limit})).all()
    return [{"role": r.role, "content": r.content, "created_at": r.created_at} for r in reversed(rows)]


@router.delete("/history")
async def clear_history(p: Principal = Depends(require("assistant:use")), s: AsyncSession = Depends(get_session)):
    await s.execute(text("DELETE FROM assistant_messages WHERE tenant_id = :t AND user_id = :u"),
                    {"t": p.tenant_id, "u": p.user_id})
    return {"ok": True}


@router.post("/chat")
async def chat(body: ChatIn, p: Principal = Depends(require("assistant:use")), s: AsyncSession = Depends(get_session)):
    msg = body.message.strip()
    await _save(s, p, "user", msg)
    eng = engine_name()
    out: dict

    if eng == "llm":
        rows = (await s.execute(text("""
            SELECT role, content FROM assistant_messages WHERE tenant_id = :t AND user_id = :u
            ORDER BY id DESC LIMIT 11"""), {"t": p.tenant_id, "u": p.user_id})).all()
        hist = [{"role": r.role, "content": r.content} for r in reversed(rows)][:-1]
        try:
            out = await engine_llm.chat(s, p, msg, hist)
        except Exception as e:  # noqa: BLE001  layanan AI mati → jatuh ke mesin aturan
            out = _rules(s, p, msg)
            out = await out
            out["notice"] = f"Asisten AI sedang tidak bisa dihubungi ({str(e)[:80]}), jadi saya pakai mode dasar."
    else:
        out = await _rules(s, p, msg)

    await _save(s, p, "assistant", out["reply"], out.get("tools_used"), out.get("engine"))
    return out


async def _rules(s: AsyncSession, p: Principal, msg: str) -> dict:
    parsed = engine_rules.parse(msg)
    if parsed is None:
        return {"reply": HELP, "cards": [], "tools_used": [], "pending_action": None, "engine": "rules"}
    name, args = parsed
    t = toolkit.REGISTRY[name]
    if t.writes:
        return {"reply": "Perintah ini mengubah data, jadi saya perlu konfirmasi Anda dulu.", "cards": [],
                "tools_used": [], "engine": "rules",
                "pending_action": {"name": name, "args": args,
                                   "confirm": t.confirm_template.format(**{k: args.get(k) or "" for k in t.params})}}
    try:
        out = await toolkit.run(s, p, name, args)
    except AppError as e:
        return {"reply": e.message, "cards": [], "tools_used": [], "pending_action": None, "engine": "rules"}
    return {"reply": out["text"], "cards": [out["card"]] if out.get("card") else [], "tools_used": [name],
            "pending_action": None, "engine": "rules"}


@router.post("/act")
async def act(body: ActIn, p: Principal = Depends(require("assistant:use")), s: AsyncSession = Depends(get_session)):
    """Jalankan perintah yang sudah dikonfirmasi pengguna. Perkakas baca tidak perlu lewat sini."""
    t = toolkit.REGISTRY.get(body.name)
    if t is None or not t.writes:
        raise AppError(422, "UNKNOWN_ACTION", "Perintah tidak dikenal")
    check_permission(p.entitlement, t.permission or "order:write")   # hormati mode baca-saja langganan
    out = await toolkit.run(s, p, body.name, body.args)
    await audit.record(s, tenant_id=p.tenant_id, actor_user_id=p.user_id, action="assistant.action",
                       entity_type="assistant", entity_id=None,
                       after={"perintah": body.name, "argumen": body.args, "hasil": out["text"]},
                       correlation_id=p.correlation_id, ip=p.ip)
    await _save(s, p, "assistant", out["text"], [body.name], engine_name())
    return {"reply": out["text"], "cards": [out["card"]] if out.get("card") else [], "executed": body.name}


