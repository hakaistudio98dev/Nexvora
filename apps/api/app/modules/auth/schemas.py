from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.security import validate_password_strength


class LoginIn(BaseModel):
    tenant: str = Field(min_length=2, max_length=63, description="Slug tenant, mis. 'acme'")
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=40, max_length=200)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str
    refresh_expires_in: int


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)

    @field_validator("new_password")
    @classmethod
    def _strong(cls, v: str) -> str:
        return validate_password_strength(v)


class TenantBrief(BaseModel):
    id: UUID
    slug: str
    name: str


class MeOut(BaseModel):
    id: UUID
    email: str
    full_name: str
    roles: list[str]
    permissions: list[str]
    tenant: TenantBrief
    subscription: dict
