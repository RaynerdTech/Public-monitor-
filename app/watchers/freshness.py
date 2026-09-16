from __future__ import annotations

from datetime import datetime, timedelta, timezone


def is_recent_timestamp(
    created_at: datetime | None,
    lookback_minutes: int,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True only for timestamped posts inside the recent lookback window.

    A tiny future tolerance avoids dropping posts when provider clocks are slightly ahead.
    Posts without a timestamp are rejected in recent-only social monitoring so an old
    undated result cannot trigger a fresh referral alert.
    """
    if created_at is None:
        return False

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)

    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(minutes=max(1, int(lookback_minutes)))
    future_tolerance = current + timedelta(minutes=2)
    return cutoff <= created_at <= future_tolerance
