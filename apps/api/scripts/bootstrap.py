"""Bootstrap sekali: tenant 'platform' + akun SUPER_ADMIN pertama.

  BOOTSTRAP_ADMIN_EMAIL=... BOOTSTRAP_ADMIN_PASSWORD=... python -m scripts.bootstrap
Opsional: --demo membuat tenant contoh 'demo' beserta admin-nya.
"""
import asyncio
import os
import sys

from sqlalchemy import select

from app.core.db import SessionLocal, set_tenant_context
from app.core.security import hash_password, validate_password_strength
from app.models import Tenant, User, UserRole


async def ensure_tenant_with_admin(slug: str, name: str, email: str, password: str, role: str) -> None:
    async with SessionLocal() as s, s.begin():
        await set_tenant_context(s, None, superadmin=True)
        t = await s.scalar(select(Tenant).where(Tenant.slug == slug))
        if t is None:
            t = Tenant(slug=slug, name=name)
            s.add(t)
            await s.flush()
            print(f"+ tenant {slug}")
        u = await s.scalar(select(User).where(User.tenant_id == t.id, User.email == email.lower()))
        if u is None:
            u = User(tenant_id=t.id, email=email.lower(), full_name=f"{name} Admin",
                     password_hash=hash_password(password))
            u.roles = [UserRole(tenant_id=t.id, role_code=role)]
            s.add(u)
            print(f"+ user {email} ({role}) di tenant {slug}")
        else:
            print(f"= user {email} sudah ada, dilewati")


async def main() -> None:
    email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL")
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
    if not email or not password:
        sys.exit("Set BOOTSTRAP_ADMIN_EMAIL dan BOOTSTRAP_ADMIN_PASSWORD")
    validate_password_strength(password)
    await ensure_tenant_with_admin("platform", "Nexvora Platform", email, password, "SUPER_ADMIN")
    if "--demo" in sys.argv:
        demo_pw = os.environ.get("DEMO_ADMIN_PASSWORD", password)
        await ensure_tenant_with_admin("demo", "Demo Brand", "admin@demo.nexvora.id", demo_pw, "TENANT_ADMIN")


if __name__ == "__main__":
    asyncio.run(main())
