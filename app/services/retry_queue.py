from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_IDS,
    TELEGRAM_ADMIN_CHAT_IDS,
    TELEGRAM_FEEDBACK_POLL_SECONDS,
    TELEGRAM_RETRY_BASE_SECONDS,
    TELEGRAM_RETRY_MAX_SECONDS,
    VALIDATION_MAX_ATTEMPTS,
    VALIDATION_QUEUE_POLL_SECONDS,
    VALIDATION_QUEUE_WORKERS,
    VALIDATION_RETRY_BASE_SECONDS,
    VALIDATION_RETRY_MAX_SECONDS,
)
from app.core.activity_log import activity
from app.core.database import (
    ensure_telegram_deliveries,
    get_sent_telegram_deliveries,
    get_referral_by_code,
    get_due_telegram_deliveries,
    get_due_validation_referrals,
    init_db,
    record_telegram_delivery,
    record_referral_feedback,
    record_validation_attempt,
)
from app.core.models import ReferralCandidate
from app.services.telegram_admin import handle_telegram_admin_message
from app.services.telegram import (
    answer_telegram_callback,
    edit_telegram_message,
    format_feedback_result,
    format_referral_alert,
    format_referral_keyboard,
    get_telegram_updates,
    parse_referral_feedback,
    send_telegram_message,
)
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


def _candidate_from_row(row: dict) -> ReferralCandidate:
    return ReferralCandidate(
        referral_url=str(row["referral_url"]),
        referral_code=str(row["referral_code"]),
        source=str(row["source"]),
        source_url=row.get("source_url"),
        post_created_at=_parse_datetime(row.get("post_created_at")),
        detected_at=_parse_datetime(row.get("detected_at"))
        or datetime.now(timezone.utc),
    )


async def _update_sent_alerts(row: dict, result: ValidationResult) -> None:
    if not TELEGRAM_BOT_TOKEN or result.status == "pending":
        return

    code = str(row["referral_code"])
    candidate = _candidate_from_row(row)
    deliveries = await get_sent_telegram_deliveries(code)
    for delivery in deliveries:
        try:
            await edit_telegram_message(
                TELEGRAM_BOT_TOKEN,
                str(delivery["chat_id"]),
                int(delivery["message_id"]),
                format_referral_alert(candidate, result),
                reply_markup=format_referral_keyboard(candidate),
            )
            activity(
                "telegram_validation_status_updated",
                referral_code=code,
                destination=str(delivery["chat_id"]),
                validation_status=result.status,
            )
        except Exception as exc:
            activity(
                "telegram_validation_status_update_failed",
                referral_code=code,
                destination=str(delivery["chat_id"]),
                error_type=type(exc).__name__,
                level="ERROR",
            )


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
    if (
        result.status in RETRYABLE_VALIDATION_STATUSES
        and prior_attempts + 1 < VALIDATION_MAX_ATTEMPTS
    ):
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
    elif result.status in RETRYABLE_VALIDATION_STATUSES:
        activity(
            "validation_stopped",
            referral_code=code,
            attempt=attempts,
            reason="maximum_attempts_reached",
            delivery_affected=False,
            level="WARNING",
        )

    await _update_sent_alerts(row, result)

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
            "telegram_delivery_not_blocked",
            referral_code=code,
            validation_status=result.status,
            reason="discovery_alert_is_independent_of_automatic_check",
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

    candidate = _candidate_from_row(row)
    result = ValidationResult(
        status=str(row.get("validation_status") or "pending"),
        campaign=row.get("campaign"),
        is_valid=True if row.get("validation_status") == "valid" else None,
    )

    try:
        message_id = await send_telegram_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            format_referral_alert(candidate, result),
            reply_markup=format_referral_keyboard(candidate),
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

    attempts = await record_telegram_delivery(
        delivery_id,
        sent=True,
        message_id=message_id,
    )
    activity(
        "telegram_delivery_sent",
        referral_code=code,
        destination=chat_id,
        attempt=attempts,
    )

    # Close the race where validation finishes after this delivery row was
    # selected but before the pending Telegram message was actually sent.
    latest = await get_referral_by_code(code)
    if latest and latest.get("status") != result.status:
        latest_result = ValidationResult(
            status=str(latest.get("status") or "pending"),
            campaign=latest.get("campaign"),
            is_valid=True if latest.get("status") == "valid" else None,
            message=latest.get("validation_message"),
            method="stored",
        )
        await _update_sent_alerts(latest, latest_result)
    return True


async def _handle_telegram_feedback(callback: dict) -> None:
    callback_id = str(callback.get("id") or "")
    parsed = parse_referral_feedback(callback.get("data"))
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")

    if not callback_id or not parsed:
        return
    if chat_id not in TELEGRAM_CHAT_IDS:
        await answer_telegram_callback(
            TELEGRAM_BOT_TOKEN,
            callback_id,
            "This chat is not authorized to update referrals.",
        )
        activity(
            "telegram_feedback_rejected",
            destination=chat_id,
            reason="unauthorized_chat",
            level="WARNING",
        )
        return

    status, code = parsed
    sender = callback.get("from") or {}
    feedback_by = str(sender.get("username") or sender.get("id") or "telegram-user")
    saved = await record_referral_feedback(code, status, feedback_by)
    if not saved:
        await answer_telegram_callback(
            TELEGRAM_BOT_TOKEN,
            callback_id,
            "Referral was not found.",
        )
        return

    label = "Worked" if status == "worked" else "Marked already used / invalid"
    await answer_telegram_callback(TELEGRAM_BOT_TOKEN, callback_id, label)

    message_id = message.get("message_id")
    original_text = str(message.get("text") or "")
    row = await get_referral_by_code(code)
    if message_id and original_text and row:
        await edit_telegram_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            int(message_id),
            format_feedback_result(original_text, status),
            reply_markup={
                "inline_keyboard": [
                    [{"text": "Open referral", "url": row["referral_url"]}]
                ]
            },
        )

    activity(
        "telegram_feedback_recorded",
        referral_code=code,
        feedback_status=status,
        destination=chat_id,
        feedback_by=feedback_by,
    )


async def run_telegram_feedback_loop() -> None:
    """Process the Worked/Invalid buttons shown under referral alerts."""
    if not TELEGRAM_BOT_TOKEN or not (TELEGRAM_CHAT_IDS or TELEGRAM_ADMIN_CHAT_IDS):
        activity("telegram_feedback_disabled", reason="telegram_not_configured")
        return

    offset: int | None = None
    activity("telegram_feedback_started")
    while True:
        try:
            updates = await get_telegram_updates(
                TELEGRAM_BOT_TOKEN,
                offset=offset,
                timeout=20,
            )
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = max(offset or 0, update_id + 1)
                callback = update.get("callback_query")
                if isinstance(callback, dict):
                    await _handle_telegram_feedback(callback)
                message = update.get("message")
                if isinstance(message, dict):
                    await handle_telegram_admin_message(message)
            if not updates:
                await asyncio.sleep(TELEGRAM_FEEDBACK_POLL_SECONDS)
        except asyncio.CancelledError:
            activity("telegram_feedback_stopped")
            raise
        except Exception as exc:
            activity(
                "telegram_feedback_error",
                error_type=type(exc).__name__,
                level="ERROR",
            )
            await asyncio.sleep(TELEGRAM_FEEDBACK_POLL_SECONDS)


async def _run_telegram_delivery_queue() -> None:
    while True:
        try:
            delivery_rows = await get_due_telegram_deliveries(limit=50)
            if delivery_rows:
                await asyncio.gather(
                    *(deliver_queued_telegram(row) for row in delivery_rows)
                )
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(VALIDATION_QUEUE_POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            activity(
                "telegram_delivery_queue_error",
                error_type=type(exc).__name__,
                level="ERROR",
            )
            await asyncio.sleep(VALIDATION_QUEUE_POLL_SECONDS)


async def _run_validation_queue() -> None:
    while True:
        try:
            validation_rows = await get_due_validation_referrals(
                limit=VALIDATION_QUEUE_WORKERS
            )
            if validation_rows:
                await asyncio.gather(
                    *(validate_queued_referral(row) for row in validation_rows)
                )
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(VALIDATION_QUEUE_POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            activity(
                "validation_queue_error",
                error_type=type(exc).__name__,
                level="ERROR",
            )
            await asyncio.sleep(VALIDATION_QUEUE_POLL_SECONDS)


async def run_retry_queues() -> None:
    """Run independent validation and Telegram queues.

    Keeping these as separate tasks guarantees that a slow Claude check cannot
    pause newly discovered Telegram alerts.
    """
    await init_db()
    activity(
        "retry_queues_started",
        validation_workers=VALIDATION_QUEUE_WORKERS,
        poll_seconds=VALIDATION_QUEUE_POLL_SECONDS,
    )

    try:
        await asyncio.gather(
            _run_telegram_delivery_queue(),
            _run_validation_queue(),
        )
    except asyncio.CancelledError:
        activity("retry_queues_stopped")
        raise
