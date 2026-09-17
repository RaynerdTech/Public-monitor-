from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock


@dataclass(frozen=True)
class UsageSnapshot:
    counts: dict[str, int]
    costs_usd: dict[str, float]
    started_at: datetime


_lock = Lock()
_counts: dict[str, int] = defaultdict(int)
_costs_usd: dict[str, float] = defaultdict(float)
_started_at = datetime.now(timezone.utc)


def record_count(key: str, amount: int = 1) -> None:
    if amount <= 0:
        return
    with _lock:
        _counts[key] += int(amount)


def record_cost_usd(key: str, amount: float | None) -> None:
    if amount is None:
        return
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return
    if value < 0:
        return
    with _lock:
        _costs_usd[key] += value


def snapshot() -> UsageSnapshot:
    with _lock:
        return UsageSnapshot(
            counts=dict(_counts),
            costs_usd=dict(_costs_usd),
            started_at=_started_at,
        )
