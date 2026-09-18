"""
Redis connection setup.

One place for the connection options, which were previously duplicated across
the data collector, the analytics service and the notification history.
"""

from __future__ import annotations

import redis

#: Fail fast rather than hanging a web request or a CronJob on an unreachable
#: Redis. The data this stores is useful but never critical.
_CONNECTION_DEFAULTS = {
    "decode_responses": True,
    "socket_connect_timeout": 5,
    "socket_timeout": 5,
    "retry_on_timeout": True,
    "health_check_interval": 30,
}


def build_redis_client(
    host: str = "redis",
    port: int = 6379,
    db: int = 0,
    username: str | None = None,
    password: str | None = None,
    verify: bool = True,
) -> redis.Redis:
    """
    Build a Redis client with the project's standard options.

    Args:
        host: Redis host.
        port: Redis port.
        db: Database number.
        username: Username, if the server requires authentication.
        password: Password, if the server requires authentication.
        verify: Ping the server before returning, so a bad connection surfaces
            here rather than at the first query.

    Returns:
        A configured client.

    Raises:
        Exception: If `verify` is set and the server cannot be reached.
    """
    options = dict(_CONNECTION_DEFAULTS, host=host, port=port, db=db)
    if username:
        options["username"] = username
    if password:
        options["password"] = password

    client = redis.Redis(**options)

    if verify:
        try:
            client.ping()
        except redis.ConnectionError as e:
            raise Exception(f"Failed to connect to Redis at {host}:{port}: {e}") from e

    return client
