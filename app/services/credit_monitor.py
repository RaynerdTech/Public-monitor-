from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import (
    APIFY_FACEBOOK_ESTIMATED_RUN_COST_USD,
    APIFY_INSTAGRAM_ESTIMATED_RUN_COST_USD,
    APIFY_MAX_RUN_COST_USD,
    APIFY_TIMEOUT_SECONDS,
    CREDIT_ALERT_THRESHOLDS_HOURS,
    CREDIT_MONITOR_INTERVAL_SECONDS,
    FACEBOOK_QUERIES,
    FACEBOOK_WATCH_INTERVAL_SECONDS,
    INSTAGRAM_QUERIES,
    INSTAGRAM_WATCH_INTERVAL_SECONDS,
    REDDIT_QUERIES,
    REDDIT_WATCH_INTERVAL_SECONDS,
    TELEGRAM_ADMIN_CHAT_IDS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_IDS,
    THREADS_QUERIES,
    THREADS_WATCH_INTERVAL_SECONDS,
)
from app.core.activity_log import activity
from app.services.apify import ApifyClient
from app.services.runtime_secrets import get_apify_token
from app.services.telegram import send_telegram_message_all


@dataclass
class _AlertState:
    alerted_thresholds: set[float] = field(default_factory=set)
    exhausted_sent: bool = False
    last_remaining: float | None = None
    last_hours_remaining: float | None = None


@dataclass
class _ApifyUsageState:
    last_usage_usd: float | None = None
    last_checked_at: datetime | None = None
    observed_burn_per_hour: float | None = None


_alert_states: dict[str, _AlertState] = {}
_apify_usage_states: dict[str, _ApifyUsageState] = {}
_last_scrape_creators_balance: int | None = None
_last_scrape_creators_hours: float | None = None
_last_apify_remaining_usd: float | None = None
_last_apify_hours: float | None = None


def _alert_destinations() -> list[str]:
    # Referral destinations are preferred so the people watching the monitor see
    # the warning. Fall back to admin chats when alerts are not configured.
    values = TELEGRAM_CHAT_IDS or TELEGRAM_ADMIN_CHAT_IDS
    return list(dict.fromkeys(value for value in values if value))


def _token_fingerprint(token: str) -> str:
    if not token:
        return "none"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _requests_per_hour(interval_seconds: int, query_count: int) -> float:
    if interval_seconds <= 0 or query_count <= 0:
        return 0.0
    return (3600.0 / float(interval_seconds)) * float(query_count)


def scrape_creators_burn_per_hour() -> float:
    # Scrape Creators currently charges one credit for each search request used
    # by the Threads and Reddit integrations.
    return _requests_per_hour(THREADS_WATCH_INTERVAL_SECONDS, len(THREADS_QUERIES)) + _requests_per_hour(
        REDDIT_WATCH_INTERVAL_SECONDS, len(REDDIT_QUERIES)
    )


def apify_estimated_burn_per_hour() -> float:
    facebook = _requests_per_hour(
        FACEBOOK_WATCH_INTERVAL_SECONDS, len(FACEBOOK_QUERIES)
    ) * APIFY_FACEBOOK_ESTIMATED_RUN_COST_USD
    instagram = _requests_per_hour(
        INSTAGRAM_WATCH_INTERVAL_SECONDS, len(INSTAGRAM_QUERIES)
    ) * APIFY_INSTAGRAM_ESTIMATED_RUN_COST_USD
    return facebook + instagram


def estimate_hours_remaining(remaining: float, burn_per_hour: float) -> float | None:
    if burn_per_hour <= 0:
        return None
    return max(0.0, float(remaining)) / burn_per_hour


def _duration_label(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours <= 0:
        return "exhausted"
    if hours < 1:
        minutes = max(1, round(hours * 60))
        return f"about {minutes} minute{'s' if minutes != 1 else ''}"
    if hours < 24:
        rounded = max(1, round(hours))
        return f"about {rounded} hour{'s' if rounded != 1 else ''}"
    days = hours / 24
    if days < 10:
        return f"about {days:.1f} days"
    return f"about {round(days)} days"


def _threshold_to_send(state: _AlertState, hours_remaining: float) -> float | None:
    eligible = [
        threshold
        for threshold in CREDIT_ALERT_THRESHOLDS_HOURS
        if hours_remaining <= threshold and threshold not in state.alerted_thresholds
    ]
    if not eligible:
        return None
    # If the monitor first sees a balance that is already low, send only the
    # closest/most urgent threshold instead of several warnings at once.
    return min(eligible)


async def _send_alert(provider: str, text: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    destinations = _alert_destinations()
    if not destinations:
        return
    successful, failures = await send_telegram_message_all(
        TELEGRAM_BOT_TOKEN,
        destinations,
        text,
    )
    activity(
        "credit_alert_sent",
        provider=provider,
        destinations=len(successful),
        failures=len(failures),
        level="WARNING",
    )


async def _evaluate_balance(
    *,
    state_key: str,
    provider: str,
    remaining: float,
    burn_per_hour: float,
    unit_label: str,
    remaining_display: str,
) -> float | None:
    state = _alert_states.setdefault(state_key, _AlertState())
    hours = estimate_hours_remaining(remaining, burn_per_hour)

    # A top-up or a newly larger allowance starts a fresh warning cycle.
    if state.last_remaining is not None and remaining > state.last_remaining:
        state.alerted_thresholds.clear()
        state.exhausted_sent = False

    state.last_remaining = remaining
    state.last_hours_remaining = hours

    if remaining <= 0:
        if not state.exhausted_sent:
            await _send_alert(
                provider,
                f"🛑 {provider} credit exhausted\n"
                f"Remaining: {remaining_display}\n"
                "Monitoring that depends on this service may stop until it is funded or the key is replaced.",
            )
            state.exhausted_sent = True
        return hours

    if hours is None:
        return hours

    threshold = _threshold_to_send(state, hours)
    if threshold is None:
        return hours

    burn_daily = burn_per_hour * 24
    await _send_alert(
        provider,
        f"⚠️ {provider} credit running low\n"
        f"Remaining: {remaining_display}\n"
        f"Current estimated use: {burn_daily:.2f} {unit_label}/day\n"
        f"Estimated time left: {_duration_label(hours)}\n"
        "Please fund the account or prepare the replacement key before monitoring stops.",
    )
    # A more urgent warning implicitly supersedes all earlier/less urgent
    # thresholds, so a late-starting monitor never backfills stale warnings.
    state.alerted_thresholds.update(
        item for item in CREDIT_ALERT_THRESHOLDS_HOURS if item >= threshold
    )
    return hours


async def observe_scrape_creators_balance(remaining_credits: int | None) -> None:
    """Observe the balance already returned by normal Scrape Creators requests."""
    global _last_scrape_creators_balance, _last_scrape_creators_hours
    if remaining_credits is None:
        return
    burn = scrape_creators_burn_per_hour()
    try:
        hours = await _evaluate_balance(
            state_key="scrape_creators",
            provider="Scrape Creators",
            remaining=float(remaining_credits),
            burn_per_hour=burn,
            unit_label="credits",
            remaining_display=f"{remaining_credits:,} credits",
        )
        _last_scrape_creators_balance = int(remaining_credits)
        _last_scrape_creators_hours = hours
    except Exception as exc:
        # Credit alerts must never break the source request that discovered the balance.
        activity(
            "credit_monitor_error",
            provider="Scrape Creators",
            error_type=type(exc).__name__,
            level="ERROR",
        )


async def _apify_limits() -> tuple[float, float, str] | None:
    token = get_apify_token().strip()
    if not token:
        return None
    client = ApifyClient(
        get_apify_token,
        timeout_seconds=min(APIFY_TIMEOUT_SECONDS, 30),
        max_run_cost_usd=APIFY_MAX_RUN_COST_USD,
    )
    payload = await client.get_limits()
    limits = payload.get("limits") if isinstance(payload.get("limits"), dict) else {}
    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    try:
        limit_usd = float(limits.get("maxMonthlyUsageUsd"))
        used_usd = float(current.get("monthlyUsageUsd"))
    except (TypeError, ValueError):
        return None
    return max(0.0, limit_usd - used_usd), max(0.0, used_usd), _token_fingerprint(token)


def _observed_apify_burn_per_hour(key: str, used_usd: float) -> float | None:
    now = datetime.now(timezone.utc)
    state = _apify_usage_states.setdefault(key, _ApifyUsageState())
    observed: float | None = None
    if state.last_usage_usd is not None and state.last_checked_at is not None:
        elapsed_hours = (now - state.last_checked_at).total_seconds() / 3600
        delta = used_usd - state.last_usage_usd
        if elapsed_hours >= (CREDIT_MONITOR_INTERVAL_SECONDS * 0.75) / 3600 and delta >= 0:
            raw = delta / elapsed_hours
            if raw > 0:
                if state.observed_burn_per_hour is None:
                    state.observed_burn_per_hour = raw
                else:
                    # Smooth stepwise Actor billing so one expensive run does not
                    # wildly change the exhaustion estimate.
                    state.observed_burn_per_hour = (state.observed_burn_per_hour * 0.65) + (raw * 0.35)
                observed = state.observed_burn_per_hour
    state.last_usage_usd = used_usd
    state.last_checked_at = now
    return observed or state.observed_burn_per_hour


async def check_apify_credit_once() -> tuple[float | None, float | None]:
    """Check the current Apify account limit without launching a paid Actor."""
    global _last_apify_remaining_usd, _last_apify_hours
    try:
        result = await _apify_limits()
        if result is None:
            return None, None
        remaining_usd, used_usd, key = result
        observed_burn = _observed_apify_burn_per_hour(key, used_usd)
        fallback_burn = apify_estimated_burn_per_hour()
        burn = observed_burn if observed_burn and observed_burn > 0 else fallback_burn
        hours = await _evaluate_balance(
            state_key=f"apify:{key}",
            provider="Apify",
            remaining=remaining_usd,
            burn_per_hour=burn,
            unit_label="USD",
            remaining_display=f"${remaining_usd:.2f}",
        )
        _last_apify_remaining_usd = remaining_usd
        _last_apify_hours = hours
        activity(
            "credit_monitor_checked",
            provider="Apify",
            remaining_usd=round(remaining_usd, 4),
            estimated_hours_remaining=round(hours, 2) if hours is not None else None,
            burn_source="observed" if observed_burn else "configured_estimate",
        )
        return remaining_usd, hours
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        activity(
            "credit_monitor_error",
            provider="Apify",
            error_type=type(exc).__name__,
            level="ERROR",
        )
        return None, None


async def run_credit_monitor() -> None:
    activity(
        "credit_monitor_started",
        interval_seconds=CREDIT_MONITOR_INTERVAL_SECONDS,
        thresholds_hours=CREDIT_ALERT_THRESHOLDS_HOURS,
    )
    while True:
        await check_apify_credit_once()
        await asyncio.sleep(CREDIT_MONITOR_INTERVAL_SECONDS)


def credit_status_text() -> str:
    lines = ["Credit status:"]
    if _last_scrape_creators_balance is None:
        lines.append("Scrape Creators: waiting for the next Threads/Reddit request.")
    else:
        lines.append(
            f"Scrape Creators: {_last_scrape_creators_balance:,} credits left"
            + (f" ({_duration_label(_last_scrape_creators_hours)} at current rate)" if _last_scrape_creators_hours is not None else "")
        )
    if _last_apify_remaining_usd is None:
        lines.append("Apify: waiting for the next balance check.")
    else:
        lines.append(
            f"Apify: ${_last_apify_remaining_usd:.2f} left before the account limit"
            + (f" ({_duration_label(_last_apify_hours)} at current rate)" if _last_apify_hours is not None else "")
        )
    return "\n".join(lines)
