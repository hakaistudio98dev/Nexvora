from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"  # development | staging | production
    app_name: str = "Nexvora API"

    # Role runtime (NOBYPASSRLS). Migrasi memakai DATABASE_MIGRATION_URL terpisah.
    database_url: str = "postgresql+asyncpg://nexvora_app:change-me@localhost:5432/nexvora"
    db_pool_size: int = 10

    redis_url: str | None = None

    jwt_secret: str = Field(default="dev-only-change-me-dev-only-change-me", min_length=32)
    jwt_issuer: str = "nexvora-api"
    jwt_audience: str = "nexvora-clients"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14

    max_failed_logins: int = 5
    lockout_minutes: int = 15
    login_rate_limit_per_minute: int = 10

    # Reservasi stok untuk order yang belum dibayar otomatis dilepas setelah durasi ini
    reservation_ttl_minutes: int = 60
    idempotency_ttl_hours: int = 24
    worker_interval_seconds: int = 30
    # Toleransi selisih berat paket terhadap berat SKU (persen)
    pack_weight_tolerance_pct: int = 10

    # ---- SaaS / langganan
    signup_enabled: bool = True
    trial_days: int = 14
    trial_plan: str = "growth"
    grace_days: int = 7
    renewal_notice_days: int = 7
    public_app_url: str = "http://localhost"
    billing_provider: str = "manual"            # manual | midtrans
    bank_transfer_info: str = "Transfer ke rekening yang tertera di invoice, lalu kirim bukti ke tim billing."
    midtrans_server_key: str | None = None
    midtrans_is_production: bool = False

    # ---- Pengiriman
    app_encryption_key: str | None = None      # Fernet key untuk kredensial kurir; wajib di production
    biteship_base_url: str = "https://api.biteship.com"
    simulator_step_seconds: int = 60           # jeda antar status pada kurir simulasi (demo)

    # ---- Notifikasi
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "Nexvora <no-reply@nexvora.local>"
    smtp_starttls: bool = True
    allow_http_webhooks: bool = False          # hanya untuk development

    cors_origins: list[str] = ["http://localhost:3000"]

    def assert_production_safe(self) -> None:
        if self.env == "production":
            if not self.app_encryption_key:
                raise RuntimeError("APP_ENCRYPTION_KEY wajib di production (Fernet key)")
            if self.billing_provider == "midtrans" and not self.midtrans_server_key:
                raise RuntimeError("MIDTRANS_SERVER_KEY wajib bila BILLING_PROVIDER=midtrans")
            if self.jwt_secret.startswith("dev-only"):
                raise RuntimeError("JWT_SECRET wajib diganti di production")
            if not self.redis_url:
                raise RuntimeError("REDIS_URL wajib di production (rate limit lintas replika)")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.assert_production_safe()
    return s
