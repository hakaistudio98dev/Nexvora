"""Fixed-window rate limiter. Redis bila REDIS_URL di-set (aman untuk banyak replika),
fallback memori proses untuk development/test."""
import time

from app.core.config import get_settings
from app.core.errors import AppError

_mem: dict[str, tuple[int, float]] = {}
_redis = None


def _get_redis():
    global _redis
    url = get_settings().redis_url
    if url and _redis is None:
        import redis.asyncio as redis
        _redis = redis.from_url(url)
    return _redis


async def hit(key: str, limit: int, window_seconds: int = 60) -> None:
    r = _get_redis()
    if r is not None:
        k = f"rl:{key}:{int(time.time() // window_seconds)}"
        n = await r.incr(k)
        if n == 1:
            await r.expire(k, window_seconds)
    else:
        now = time.time()
        count, start = _mem.get(key, (0, now))
        if now - start >= window_seconds:
            count, start = 0, now
        count += 1
        _mem[key] = (count, start)
        n = count
    if n > limit:
        raise AppError(429, "RATE_LIMITED", "Terlalu banyak percobaan. Coba lagi sebentar lagi.")


def reset_memory() -> None:
    _mem.clear()
