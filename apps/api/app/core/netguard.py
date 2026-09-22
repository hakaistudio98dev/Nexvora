"""Perlindungan SSRF untuk URL yang diisi pengguna (webhook): hanya host publik."""
import ipaddress
import socket
from urllib.parse import urlparse

from app.core.config import get_settings
from app.core.errors import AppError


def resolve(host: str) -> list[str]:
    return list({ai[4][0] for ai in socket.getaddrinfo(host, None)})


def validate_public_url(url: str) -> str:
    u = urlparse(url)
    allow_http = get_settings().allow_http_webhooks and get_settings().env != "production"
    if u.scheme not in (("https", "http") if allow_http else ("https",)) or not u.hostname:
        raise AppError(422, "INVALID_URL", "URL webhook harus https://")
    if u.username or u.password:
        raise AppError(422, "INVALID_URL", "URL tidak boleh memuat username/password")
    try:
        addrs = resolve(u.hostname)
    except OSError:
        raise AppError(422, "UNRESOLVABLE_HOST", f"Host {u.hostname} tidak bisa ditemukan") from None
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified):
            raise AppError(422, "PRIVATE_ADDRESS", "URL webhook harus mengarah ke alamat internet publik")
    return url
