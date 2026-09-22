"""State machine order (PRD §9). Satu-satunya sumber kebenaran transisi yang sah."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    name: str
    label: str
    from_statuses: tuple[str, ...]
    to_status: str | None      # None = aksi tanpa perubahan status (mis. coba reservasi ulang)
    permission: str


ACTIONS: dict[str, Action] = {a.name: a for a in [
    Action("mark_paid", "Tandai sudah dibayar", ("CREATED",), "PAID", "order:write"),
    Action("reserve", "Coba reservasi stok lagi", ("CREATED", "PAID"), None, "order:write"),
    Action("allocate", "Alokasikan ke gudang", ("PAID",), "ALLOCATED", "order:write"),
    # Membuat task picking per bin (wave berisi 1 order). Packing & "siap kirim" hanya lewat
    # packing station WMS agar item dan berat selalu terverifikasi.
    Action("start_picking", "Buat task picking", ("ALLOCATED",), "PICKING", "order:fulfill"),
    Action("ship", "Serahkan ke kurir", ("READY_TO_SHIP",), "SHIPPED", "order:fulfill"),
    Action("deliver", "Tandai diterima", ("SHIPPED",), "DELIVERED", "order:fulfill"),
    Action("mark_failed", "Pembayaran gagal", ("CREATED", "PAID"), "FAILED", "order:write"),
    Action("cancel", "Batalkan order", ("CREATED", "PAID", "ALLOCATED"), "CANCELLED", "order:write"),
]}

TERMINAL = {"DELIVERED", "CANCELLED", "FAILED", "REFUNDED"}
ACTIVE_STATUSES = ("CREATED", "PAID", "ALLOCATED", "PICKING", "PACKING", "READY_TO_SHIP", "SHIPPED")


def allowed_actions(status: str, stock_status: str, permissions: set[str]) -> list[Action]:
    out = []
    for a in ACTIONS.values():
        if status not in a.from_statuses or a.permission not in permissions:
            continue
        if a.name == "reserve" and stock_status == "RESERVED":
            continue
        if a.name == "allocate" and stock_status != "RESERVED":
            continue
        out.append(a)
    return out
