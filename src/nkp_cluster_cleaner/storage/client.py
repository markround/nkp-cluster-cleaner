"""
Redis connection setup.

One place for the connection options, which were previously duplicated across
the data collector, the analytics service and the notification history.
"""

from __future__ import annotations

import redis

from ..core.settings import RedisSettings

#: Fail fast rather than hanging a web request or a CronJob on an unreachable
#: Redis. The data this stores is useful but never critical.
#:
#: Timeouts are still retried: redis-py's default Retry covers TimeoutError and
#: ConnectionError, which is why `retry_on_timeout` was deprecated in 6.0 and is
#: not set here.
_CONNECTION_DEFAULTS = {
    "decode_responses": True,
    "socket_connect_timeout": 5,
    "socket_timeout": 5,
    "health_check_interval": 30,
}


def build_redis_client(
    settings: RedisSettings | None = None, verify: bool = True
) -> redis.Redis:
    """
    Build a Redis client with the project's standard options.

    Args:
        settings: Connection details. Defaults to the built-in defaults, which
            match the in-cluster service name.
        verify: Ping the server before returning, so a bad connection surfaces
            here rather than at the first query.

    Returns:
        A configured client.

    Raises:
        Exception: If `verify` is set and the server cannot be reached.
    """
    settings = settings or RedisSettings()

    options = dict(
        _CONNECTION_DEFAULTS,
        host=settings.host,
        port=settings.port,
        db=settings.db,
    )
    if settings.username:
        options["username"] = settings.username
    if settings.password:
        options["password"] = settings.password

    client = redis.Redis(**options)

    if verify:
        try:
            client.ping()
        except redis.ConnectionError as e:
            raise Exception(f"Failed to connect to Redis at {settings}: {e}") from e

    return client
