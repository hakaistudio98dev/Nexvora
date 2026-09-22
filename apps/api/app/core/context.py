from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Ctx:
    """Siapa yang melakukan aksi — dibawa ke ledger, history, dan audit."""
    tenant_id: UUID
    actor_user_id: UUID | None = None
    correlation_id: str | None = None
    ip: str | None = None

    @classmethod
    def from_principal(cls, p) -> "Ctx":  # noqa: ANN001
        return cls(tenant_id=p.tenant_id, actor_user_id=p.user_id, correlation_id=p.correlation_id, ip=p.ip)
