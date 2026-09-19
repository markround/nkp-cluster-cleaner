"""
Settings objects passed between layers.

These live in `core` so that both the CLI and the storage and notification
layers can use them without depending on each other. They exist mainly so that
connection details stop being threaded through every function signature as five
separate parameters.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RedisSettings:
    """Where to find Redis."""

    host: str = "redis"
    port: int = 6379
    db: int = 0
    username: str | None = None
    password: str | None = None

    @classmethod
    def from_kwargs(cls, **kwargs) -> RedisSettings:
        """Build from the click parameter names (`redis_host`, `redis_port`, …)."""
        return cls(
            host=kwargs.get("redis_host") or "redis",
            port=kwargs.get("redis_port") or 6379,
            db=kwargs.get("redis_db") or 0,
            username=kwargs.get("redis_username"),
            password=kwargs.get("redis_password"),
        )

    def __str__(self) -> str:
        return f"{self.host}:{self.port} (db {self.db})"


@dataclass(frozen=True)
class SlackSettings:
    """Slack delivery parameters."""

    token: str | None = None
    channel: str | None = None
    username: str = "NKP Cluster Cleaner"
    icon_emoji: str = ":broom:"

    @classmethod
    def from_kwargs(cls, **kwargs) -> SlackSettings:
        """Build from the click parameter names (`slack_token`, `slack_channel`, …)."""
        return cls(
            token=kwargs.get("slack_token"),
            channel=kwargs.get("slack_channel"),
            username=kwargs.get("slack_username") or "NKP Cluster Cleaner",
            icon_emoji=kwargs.get("slack_icon_emoji") or ":broom:",
        )

    def as_backend_kwargs(self) -> dict:
        """Render as the keyword arguments the notification backend expects."""
        return {
            "token": self.token,
            "channel": self.channel,
            "username": self.username,
            "icon_emoji": self.icon_emoji,
        }
