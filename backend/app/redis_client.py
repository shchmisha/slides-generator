from functools import lru_cache

from redis import Redis

from app.config import get_settings


@lru_cache
def get_redis() -> Redis:
    url = get_settings().redis_url.get_secret_value()
    return Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
