import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_CID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cid = request.headers.get("x-correlation-id", "")
        if not _CID_RE.match(cid):
            cid = uuid.uuid4().hex
        request.state.correlation_id = cid
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Cache-Control", "no-store")
        h.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
            h.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        return response


def client_ip(request: Request) -> str:
    # Di belakang Nginx, X-Real-IP di-set oleh proxy tepercaya
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
