from __future__ import annotations

import asyncio
import hashlib
import httpx
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
    EXA_QUERIES,
    EXA_SEARCH_PRICE_USD,
    EXA_WATCH_INTERVAL_SECONDS,
    INSTAGRAM_QUERIES,
    INSTAGRAM_WATCH_INTERVAL_SECONDS,
    PODCAST_INDEX_API_KEY,
    REDDIT_QUERIES,
    REDDIT_WATCH_INTERVAL_SECONDS,
    TELEGRAM_ADMIN_CHAT_IDS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_IDS,
    THREADS_QUERIES,
    THREADS_WATCH_INTERVAL_SECONDS,
    WEB_DIRECT_SOURCES,
    X_BEARER_TOKEN,
    YOUTUBE_DAILY_SEARCH_QUOTA,
    YOUTUBE_QUERIES,
    YOUTUBE_WATCH_INTERVAL_SECONDS,
)
from app.core.activity_log import activity
from app.services.apify import ApifyClient
from app.services.runtime_secrets import get_apify_token
from app.services.telegram import send_telegram_message_all
from app.services.usage_registry import snapshot


@dataclass
class _AlertState:
    alerted_thresholds: set[float] = field(default_factory=set)
    exhausted_sent: bool = False
    last_remaining: float | None = None
    last_hours_remaining: float | None = None


@dataclass
class _ApifyUsageState:
    baseline_usage_usd: float | None = None
    baseline_checked_at: datetime | None = None
    last_usage_usd: float | None = None
    last_checked_at: datetime | None = None
    observed_burn_per_hour: float | None = None


_alert_states: dict[str, _AlertState] = {}
_apify_usage_states: dict[str, _ApifyUsageState] = {}
_last_scrape_creators_balance: int | None = None
_last_scrape_creators_hours: float | None = None
_scrape_creators_suspect_balance: int | None = None
_last_apify_remaining_usd: float | None = None
_last_apify_hours: float | None = None
_last_x_total_balance_usd: float | None = None
_last_x_project_usage: int | None = None
_last_x_project_cap: int | None = None
_last_x_status: str = "not checked"


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

    # Only a meaningful top-up starts a fresh warning cycle. Small increases can
    # happen when concurrent requests finish out of order and must not cause the
    # same low-credit warning to fire again.
    if state.last_remaining is not None and remaining > state.last_remaining:
        increase = remaining - state.last_remaining
        reset_threshold = max(1.0, abs(state.last_remaining) * 0.02)
        if increase >= reset_threshold:
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


def _scrape_balance_is_suspicious(previous: int | None, current: int) -> bool:
    """Reject one-off catastrophic drops until a second request confirms them."""
    if previous is None or previous < 100:
        return False
    return current < (previous * 0.25)


async def observe_scrape_creators_balance(
    remaining_credits: int | None,
    *,
    credits_charged: int = 0,
) -> None:
    """Observe the balance already returned by normal Scrape Creators requests.

    A few Scrape Creators endpoints can occasionally surface a transient/incorrect
    balance. A single >75% drop is therefore held for confirmation instead of
    immediately alarming Telegram. The next similarly-low reading confirms it.
    """
    global _last_scrape_creators_balance, _last_scrape_creators_hours
    global _scrape_creators_suspect_balance
    if remaining_credits is None:
        return

    remaining_credits = int(remaining_credits)
    previous = _last_scrape_creators_balance

    # A Render deploy/restart clears all in-memory alert state. Do not send a
    # low-credit warning from the very first Scrape Creators balance observed
    # after startup. Treat it as a baseline and require the next normal source
    # request to confirm a genuinely low balance. This prevents a transient
    # startup response (for example 42 credits followed by the real 7,000+)
    # from creating a false Telegram alert after every deploy.
    if previous is None:
        burn = scrape_creators_burn_per_hour()
        _last_scrape_creators_balance = remaining_credits
        _last_scrape_creators_hours = estimate_hours_remaining(float(remaining_credits), burn)
        activity(
            "credit_balance_baseline_initialized",
            provider="Scrape Creators",
            remaining_credits=remaining_credits,
        )
        return

    if _scrape_balance_is_suspicious(previous, remaining_credits):
        suspect = _scrape_creators_suspect_balance
        confirmation_tolerance = max(10, int(max(1, suspect or remaining_credits) * 0.05))
        if suspect is None or abs(remaining_credits - suspect) > confirmation_tolerance:
            _scrape_creators_suspect_balance = remaining_credits
            activity(
                "credit_balance_anomaly_ignored",
                provider="Scrape Creators",
                previous_balance=previous,
                reported_balance=remaining_credits,
                credits_charged=credits_charged,
                level="WARNING",
            )
            return
        # Two consecutive low readings agree closely enough: accept the real drop.
        _scrape_creators_suspect_balance = None
    else:
        _scrape_creators_suspect_balance = None

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
        _last_scrape_creators_balance = remaining_credits
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
    """Estimate Apify burn from a stable window, not one five-minute sample.

    Actor charges are stepwise. Extrapolating one short interval can turn a single
    expensive run into a nonsense $40+/day forecast. We require at least 30
    minutes of observations from the current token before using measured burn.
    """
    now = datetime.now(timezone.utc)
    state = _apify_usage_states.setdefault(key, _ApifyUsageState())

    if (
        state.baseline_usage_usd is None
        or state.baseline_checked_at is None
        or used_usd < state.baseline_usage_usd
    ):
        state.baseline_usage_usd = used_usd
        state.baseline_checked_at = now
        state.observed_burn_per_hour = None

    state.last_usage_usd = used_usd
    state.last_checked_at = now

    elapsed_hours = (now - state.baseline_checked_at).total_seconds() / 3600
    if elapsed_hours < 0.5:
        return None

    delta = used_usd - state.baseline_usage_usd
    if delta <= 0:
        return state.observed_burn_per_hour

    raw = delta / elapsed_hours
    fallback = apify_estimated_burn_per_hour()
    # The configured estimate is based on our measured Actor costs. Keep the
    # observed correction conservative so manual tests/startup bursts do not
    # create wild Telegram forecasts.
    if fallback > 0:
        raw = min(raw, fallback * 2.0)

    if state.observed_burn_per_hour is None:
        state.observed_burn_per_hour = raw
    else:
        state.observed_burn_per_hour = (state.observed_burn_per_hour * 0.75) + (raw * 0.25)
    return state.observed_burn_per_hour


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
        # Never forecast lower than our configured measured-cost estimate. Once
        # a 30-minute observation window exists, allow it to raise the estimate.
        burn = max(fallback_burn, observed_burn or 0.0)
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


async def check_x_credit_once() -> tuple[float | None, int | None, int | None]:
    """Fetch X credit balance and monthly Post usage when the account exposes them."""
    global _last_x_total_balance_usd, _last_x_project_usage, _last_x_project_cap, _last_x_status
    if not X_BEARER_TOKEN:
        _last_x_status = "not configured"
        return None, None, None

    headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}", "Accept": "application/json"}
    balance = None
    project_usage = None
    project_cap = None
    statuses: list[str] = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
        try:
            response = await client.get("https://api.x.com/2/usage/credits")
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict):
                raw = data.get("total_balance")
                if raw is not None:
                    balance = float(raw)
                    _last_x_total_balance_usd = balance
            statuses.append("credit balance ok")
        except Exception as exc:
            statuses.append(f"credit balance unavailable ({type(exc).__name__})")

        try:
            response = await client.get(
                "https://api.x.com/2/usage/tweets",
                params={"days": 30, "usage.fields": "project_cap,project_usage"},
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict):
                if data.get("project_usage") is not None:
                    project_usage = int(data.get("project_usage"))
                    _last_x_project_usage = project_usage
                if data.get("project_cap") is not None:
                    project_cap = int(data.get("project_cap"))
                    _last_x_project_cap = project_cap
            statuses.append("usage ok")
        except Exception as exc:
            statuses.append(f"usage unavailable ({type(exc).__name__})")

    _last_x_status = "; ".join(statuses)
    activity(
        "credit_monitor_checked",
        provider="X",
        total_balance_usd=balance,
        project_usage=project_usage,
        project_cap=project_cap,
    )
    return balance, project_usage, project_cap


async def refresh_credit_status() -> None:
    await asyncio.gather(
        check_apify_credit_once(),
        check_x_credit_once(),
    )


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
    usage = snapshot()
    lines = ["Credit / quota status:"]

    if _last_scrape_creators_balance is None:
        lines.append("Scrape Creators (Threads + Reddit): waiting for next API response.")
    else:
        lines.append(
            f"Scrape Creators (Threads + Reddit): {_last_scrape_creators_balance:,} credits left"
            + (f" ({_duration_label(_last_scrape_creators_hours)} at current rate)" if _last_scrape_creators_hours is not None else "")
        )

    if _last_apify_remaining_usd is None:
        lines.append("Apify (Facebook + Instagram): waiting for balance check.")
    else:
        lines.append(
            f"Apify (Facebook + Instagram): ${_last_apify_remaining_usd:.2f} left before account limit"
            + (f" ({_duration_label(_last_apify_hours)} at current rate)" if _last_apify_hours is not None else "")
        )

    if _last_x_total_balance_usd is not None:
        x_line = f"X: ${_last_x_total_balance_usd:.2f} credit balance"
    else:
        x_line = f"X: {_last_x_status}"
    if _last_x_project_usage is not None and _last_x_project_cap:
        x_line += f"; {_last_x_project_usage:,}/{_last_x_project_cap:,} monthly Posts used"
    lines.append(x_line)

    youtube_search_calls = usage.counts.get("youtube.search_calls", 0)
    expected_youtube_calls = round((86400 / YOUTUBE_WATCH_INTERVAL_SECONDS) * max(1, len(YOUTUBE_QUERIES)))
    lines.append(
        f"YouTube: {youtube_search_calls} search call(s) since restart; configured ~{expected_youtube_calls}/day; "
        f"quota setting {YOUTUBE_DAILY_SEARCH_QUOTA}/day. Live remaining quota is only available in Google Cloud Console."
    )

    exa_search_calls = usage.counts.get("exa.search_requests", 0)
    exa_content_calls = usage.counts.get("exa.contents_requests", 0)
    exa_runtime_cost = usage.costs_usd.get("exa.total", 0.0)
    expected_exa_searches = (86400 / EXA_WATCH_INTERVAL_SECONDS) * max(1, len(EXA_QUERIES))
    expected_exa_search_cost = expected_exa_searches * EXA_SEARCH_PRICE_USD
    lines.append(
        f"Exa web: {exa_search_calls} search + {exa_content_calls} content request(s) since restart; "
        f"API-reported runtime cost ${exa_runtime_cost:.4f}; configured search cadence ~${expected_exa_search_cost:.2f}/day before fallback content fetches. "
        "Account balance is not exposed by the documented Search API; check Exa dashboard for the live balance."
    )

    podcast_requests = usage.counts.get("podcast_index.requests", 0)
    if PODCAST_INDEX_API_KEY:
        lines.append(
            f"Podcast Index: {podcast_requests} API request(s) since restart; core index is free and has no credit balance to report."
        )
    else:
        lines.append("Podcast Index: not configured.")

    if WEB_DIRECT_SOURCES:
        lines.append(f"Direct websites: no paid API credits; {len(WEB_DIRECT_SOURCES)} configured source(s).")
    else:
        lines.append("Direct websites: no paid API credits; no sources configured.")
    lines.append("Telegram Bot API: no credit balance.")
    return "\n".join(lines)
