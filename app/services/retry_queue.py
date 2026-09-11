from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_IDS,
    TELEGRAM_RETRY_BASE_SECONDS,
    TELEGRAM_RETRY_MAX_SECONDS,
    VALIDATION_QUEUE_POLL_SECONDS,
    VALIDATION_QUEUE_WORKERS,
    VALIDATION_RETRY_BASE_SECONDS,
    VALIDATION_RETRY_MAX_SECONDS,
)
from app.core.activity_log import activity
from app.core.database import (
    ensure_telegram_deliveries,
    get_due_telegram_deliveries,
    get_due_validation_referrals,
    init_db,
    record_telegram_delivery,
    record_validation_attempt,
)
from app.core.models import ReferralCandidate
from app.services.telegram import format_referral_alert, send_telegram_message
from app.services.validator import ValidationResult, validate_referral


RETRYABLE_VALIDATION_STATUSES = {"pending", "blocked", "error", "unknown"}


def _retry_delay(attempts_completed: int, base: int, maximum: int) -> int:
    exponent = min(max(0, attempts_completed - 1), 8)
    return min(maximum, base * (2**exponent))


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def validate_queued_referral(row: dict) -> ValidationResult:
    code = str(row["referral_code"])
    prior_attempts = int(row.get("validation_attempts") or 0)
    activity(
        "validation_started",
        referral_code=code,
        attempt=prior_attempts + 1,
        previous_status=row.get("status"),
        source=row.get("source"),
    )

    try:
        result = await validate_referral(code)
    except Exception as exc:
        result = ValidationResult(
            status="error",
            message=f"Validator raised {type(exc).__name__}",
            method="internal",
        )

    retry_after = None
    if result.status in RETRYABLE_VALIDATION_STATUSES:
        retry_after = _retry_delay(
            prior_attempts + 1,
            VALIDATION_RETRY_BASE_SECONDS,
            VALIDATION_RETRY_MAX_SECONDS,
        )

    attempts = await record_validation_attempt(
        code,
        result.status,
        result.campaign,
        result.message,
        retry_after_seconds=retry_after,
    )
    activity(
        "validation_completed",
        referral_code=code,
        status=result.status,
        method=result.method,
        campaign=result.campaign,
        attempt=attempts,
        retry_in_seconds=retry_after,
        level="WARNING" if result.status in RETRYABLE_VALIDATION_STATUSES else "INFO",
    )
    if retry_after is not None:
        activity(
            "validation_retry_scheduled",
            referral_code=code,
            attempt=attempts,
            retry_in_seconds=retry_after,
            reason=result.status,
            level="WARNING",
        )

    if result.status == "valid":
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS:
            await ensure_telegram_deliveries(code, TELEGRAM_CHAT_IDS)
            activity(
                "telegram_delivery_queued",
                referral_code=code,
                destinations=len(TELEGRAM_CHAT_IDS),
            )
        else:
            activity(
                "telegram_not_configured",
                referral_code=code,
                level="ERROR",
            )
    else:
        activity(
            "telegram_not_sent",
            referral_code=code,
            validation_status=result.status,
            reason="only_confirmed_valid_referrals_are_sent",
        )

    return result


async def deliver_queued_telegram(row: dict) -> bool:
    delivery_id = int(row["delivery_id"])
    code = str(row["referral_code"])
    prior_attempts = int(row.get("attempts") or 0)
    chat_id = str(row["chat_id"])
    activity(
        "telegram_delivery_started",
        referral_code=code,
        destination=chat_id,
        attempt=prior_attempts + 1,
    )

    candidate = ReferralCandidate(
        referral_url=str(row["referral_url"]),
        referral_code=code,
        source=str(row["source"]),
        source_url=row.get("source_url"),
        post_created_at=_parse_datetime(row.get("post_created_at")),
        detected_at=_parse_datetime(row.get("detected_at"))
        or datetime.now(timezone.utc),
    )
    result = ValidationResult(
        status="valid",
        campaign=row.get("campaign"),
        is_valid=True,
    )

    try:
        await send_telegram_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            format_referral_alert(candidate, result),
        )
    except Exception as exc:
        retry_after = _retry_delay(
            prior_attempts + 1,
            TELEGRAM_RETRY_BASE_SECONDS,
            TELEGRAM_RETRY_MAX_SECONDS,
        )
        attempts = await record_telegram_delivery(
            delivery_id,
            sent=False,
            error=type(exc).__name__,
            retry_after_seconds=retry_after,
        )
        activity(
            "telegram_delivery_failed",
            referral_code=code,
            destination=chat_id,
            attempt=attempts,
            error_type=type(exc).__name__,
            retry_in_seconds=retry_after,
            level="ERROR",
        )
        return False

    attempts = await record_telegram_delivery(delivery_id, sent=True)
    activity(
        "telegram_delivery_sent",
        referral_code=code,
        destination=chat_id,
        attempt=attempts,
    )
    return True


async def run_retry_queues() -> None:
    """Continuously resolve validation and Telegram work without blocking watchers."""
    await init_db()
    activity(
        "retry_queues_started",
        validation_workers=VALIDATION_QUEUE_WORKERS,
        poll_seconds=VALIDATION_QUEUE_POLL_SECONDS,
    )

    while True:
        try:
            validation_rows = await get_due_validation_referrals(
                limit=VALIDATION_QUEUE_WORKERS
            )
            if validation_rows:
                await asyncio.gather(
                    *(validate_queued_referral(row) for row in validation_rows)
                )

            delivery_rows = await get_due_telegram_deliveries(limit=50)
            if delivery_rows:
                await asyncio.gather(
                    *(deliver_queued_telegram(row) for row in delivery_rows)
                )

            if not validation_rows and not delivery_rows:
                await asyncio.sleep(VALIDATION_QUEUE_POLL_SECONDS)
            else:
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            activity("retry_queues_stopped")
            raise
        except Exception as exc:
            activity(
                "retry_queue_error",
                error_type=type(exc).__name__,
                level="ERROR",
            )
            await asyncio.sleep(VALIDATION_QUEUE_POLL_SECONDS)
