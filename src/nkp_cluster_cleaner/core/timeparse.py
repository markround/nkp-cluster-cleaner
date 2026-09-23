"""
Parsing and formatting of the time values the tool works with.

Everything here is timezone-aware UTC. The Kubernetes API reports timestamps in
UTC, so comparing them against a naive local `datetime.now()` - as this tool
used to - skews every expiry and grace decision by the machine's UTC offset.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

#: <number><unit>, where unit is hours, days, weeks or years.
_PERIOD_PATTERN = re.compile(r"^(\d+)([dhwy])$")

_UNIT_DELTAS = {
    "h": lambda n: timedelta(hours=n),
    "d": lambda n: timedelta(days=n),
    "w": lambda n: timedelta(weeks=n),
    "y": lambda n: timedelta(days=n * 365),
}


def now() -> datetime:
    """The current time, as timezone-aware UTC."""
    return datetime.now(UTC)


def parse_period(period: str) -> timedelta:
    """
    Parse a duration such as "1d", "4h", "2w" or "1y".

    Args:
        period: The duration string. Case-insensitive, surrounding whitespace
            is ignored.

    Returns:
        The equivalent timedelta.

    Raises:
        ValueError: If the string is not a supported duration.
    """
    match = _PERIOD_PATTERN.match(period.strip().lower())
    if not match:
        raise ValueError(
            "Invalid format. Expected format: <number><unit> where unit is "
            "d/w/h/y (e.g., '1d', '2w', '48h', '1y')"
        )

    number, unit = match.groups()
    return _UNIT_DELTAS[unit](int(number))


def parse_timestamp(timestamp: str | datetime) -> datetime:
    """
    Parse an RFC3339 timestamp from the Kubernetes API into aware UTC.

    The Python client returns `datetime` objects for typed resources but plain
    strings for custom resources, so both are accepted. A value that carries no
    offset is assumed to be UTC, which is what the API always emits.

    Args:
        timestamp: Value such as "2026-09-11T15:20:08Z", or a datetime.

    Returns:
        A timezone-aware datetime in UTC.

    Raises:
        ValueError: If the string cannot be parsed.
    """
    if isinstance(timestamp, datetime):
        parsed = timestamp
    else:
        try:
            # fromisoformat handles the "Z" suffix from Python 3.11 onwards.
            parsed = datetime.fromisoformat(timestamp)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid creation timestamp format: {timestamp}") from e

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)

    return parsed.astimezone(UTC)


def expiry_from(created_at: datetime, period: str) -> datetime:
    """
    Work out when something created at `created_at` expires after `period`.

    Args:
        created_at: Creation time (aware).
        period: A duration string such as "30d".

    Returns:
        The expiry time in UTC.

    Raises:
        ValueError: If `period` is not a supported duration.
    """
    return created_at + parse_period(period)


def format_duration(delta: timedelta) -> str:
    """
    Render a duration approximately, for human-readable status messages.

    Rounds towards the coarser unit, so "2d" rather than "2d 3h". Durations of
    exactly one day keep their hours, since "1d" alone reads as less precise
    than it is at that scale.

    Args:
        delta: The duration to render.

    Returns:
        A short string such as "5d", "1d 4h", "7h" or "0h".
    """
    if delta.total_seconds() < 0:
        return "0h"

    days = delta.days
    hours = delta.seconds // 3600

    if days > 1:
        return f"{days}d"
    if days == 1:
        return f"1d {hours}h" if hours else "1d"
    return f"{hours}h"
