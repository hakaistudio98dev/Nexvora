"""Mesin berbasis model bahasa (Claude) dengan tool use.

Batas yang dipegang:
- Model hanya boleh memanggil perkakas yang memang tersedia untuk peran & paket pengguna.
- Perkakas yang mengubah data TIDAK PERNAH dijalankan model; kalau dipanggil, permintaannya dikembalikan
  sebagai usulan yang harus dikonfirmasi pengguna lewat tombol.
- Isi percakapan pengguna dikirim ke API Anthropic. Kalau kunci tidak diisi, sistem otomatis memakai mesin aturan
  sehingga tidak ada data yang keluar.
"""
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.assistant import tools as toolkit

API_URL = "https://api.anthropic.com/v1/messages"
MAX_ROUNDS = 4
_TRANSPORT: httpx.AsyncBaseTransport | None = None  # dipakai tes

SYSTEM = """Kamu asisten operasional di dalam aplikasi fulfillment Nexvora. Pengguna adalah pemilik toko atau staf gudang di Indonesia.

Aturan:
- Jawab dalam bahasa Indonesia yang sederhana, singkat, dan langsung ke inti. Hindari istilah teknis.
- Untuk pertanyaan tentang data (stok, order, pengiriman, kinerja), WAJIB panggil perkakas. Jangan pernah mengarang angka.
- Kalau perkakas mengembalikan data kosong, katakan apa adanya.
- Untuk perintah yang mengubah data, panggil perkakasnya; sistem akan meminta konfirmasi pengguna sebelum dijalankan.
- Jangan menjanjikan tindakan yang tidak ada perkakasnya. Kalau tidak bisa, katakan apa yang bisa kamu bantu.
- Jangan mengulang seluruh isi tabel; ringkas intinya saja karena tabelnya sudah ditampilkan ke pengguna."""


def schema(t: toolkit.Tool) -> dict:
    return {"name": t.name, "description": t.description,
            "input_schema": {"type": "object", "properties": t.params,
                             "required": [k for k, v in t.params.items() if v.get("required")]}}


async def _call(body: dict) -> dict:
    st = get_settings()
    headers = {"x-api-key": st.anthropic_api_key or "", "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=45, transport=_TRANSPORT) as c:
        r = await c.post(API_URL, json=body, headers=headers)
    if r.status_code >= 300:
        raise RuntimeError(f"API asisten menolak permintaan (HTTP {r.status_code})")
    return r.json()


async def chat(s: AsyncSession, p, message: str, history: list[dict]) -> dict:  # noqa: ANN001
    st = get_settings()
    allowed = {t.name: t for t in toolkit.available(p)}
    messages = [*history, {"role": "user", "content": message}]
    cards, used, pending = [], [], None

    for _ in range(MAX_ROUNDS):
        data = await _call({"model": st.assistant_model, "max_tokens": 1024, "system": SYSTEM,
                            "messages": messages, "tools": [schema(t) for t in allowed.values()]})
        blocks = data.get("content", [])
        calls = [b for b in blocks if b.get("type") == "tool_use"]
        if not calls:
            text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            return {"reply": text or "Maaf, saya belum menangkap maksudnya.", "cards": cards, "tools_used": used,
                    "pending_action": pending, "engine": "llm"}

        messages.append({"role": "assistant", "content": blocks})
        results = []
        for c in calls:
            name, args = c["name"], c.get("input") or {}
            t = allowed.get(name)
            if t is None:
                results.append({"type": "tool_result", "tool_use_id": c["id"], "is_error": True,
                                "content": "Perkakas itu tidak tersedia untuk pengguna ini."})
                continue
            if t.writes:
                # tidak dijalankan: kembalikan sebagai usulan yang menunggu konfirmasi pengguna
                pending = {"name": name, "args": args,
                           "confirm": t.confirm_template.format(**{k: args.get(k, "") for k in t.params}) or t.description}
                results.append({"type": "tool_result", "tool_use_id": c["id"],
                                "content": "Perintah sudah disiapkan dan menunggu konfirmasi pengguna. "
                                           "Beri tahu pengguna untuk menekan tombol konfirmasi."})
                continue
            try:
                out = await toolkit.run(s, p, name, args)
                used.append(name)
                if out.get("card"):
                    cards.append(out["card"])
                results.append({"type": "tool_result", "tool_use_id": c["id"], "content": out.get("text", "")})
            except Exception as e:  # noqa: BLE001
                results.append({"type": "tool_result", "tool_use_id": c["id"], "is_error": True,
                                "content": str(getattr(e, "message", e))[:300]})
        messages.append({"role": "user", "content": results})

    return {"reply": "Permintaannya terlalu panjang untuk diproses sekaligus. Coba pecah jadi pertanyaan yang lebih spesifik.",
            "cards": cards, "tools_used": used, "pending_action": pending, "engine": "llm"}
