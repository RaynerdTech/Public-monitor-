from collections.abc import Awaitable, Callable

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS
from app.core.activity_log import activity
from app.core.database import init_db, record_validation_attempt, save_referral
from app.core.extractor import extract_referral_code, extract_referral_links
from app.core.models import ReferralCandidate
from app.services.telegram import format_referral_alert, send_telegram_message_all
from app.services.validator import ValidationResult, validate_referral
from app.watchers.base import SourcePost


Validator = Callable[[str], Awaitable[ValidationResult]]


async def process_post(
    post: SourcePost,
    *,
    validator: Validator = validate_referral,
    alert_valid: bool = True,
    defer_validation: bool = False,
) -> list[tuple[ReferralCandidate, ValidationResult, bool]]:
    await init_db()
    results: list[tuple[ReferralCandidate, ValidationResult, bool]] = []

    for referral_url in extract_referral_links(post.text):
        code = extract_referral_code(referral_url)
        if not code:
            continue

        candidate = ReferralCandidate(
            referral_url=referral_url,
            referral_code=code,
            source=post.source,
            source_url=post.url,
            post_created_at=post.created_at,
        )

        is_new = await save_referral(candidate)
        if not is_new:
            duplicate = ValidationResult(status="duplicate")
            activity(
                "referral_duplicate",
                referral_code=code,
                source=post.source,
                source_url=post.url,
            )
            results.append((candidate, duplicate, False))
            continue

        activity(
            "referral_discovered",
            referral_code=code,
            source=post.source,
            source_url=post.url,
        )

        if defer_validation:
            queued = ValidationResult(
                status="pending",
                message="Queued for background validation",
                method="queue",
            )
            activity(
                "validation_queued",
                referral_code=code,
                source=post.source,
            )
            results.append((candidate, queued, True))
            continue

        activity(
            "validation_started",
            referral_code=code,
            attempt=1,
            source=post.source,
        )
        validation = await validator(code)
        await record_validation_attempt(
            code,
            validation.status,
            validation.campaign,
            validation.message,
        )
        activity(
            "validation_completed",
            referral_code=code,
            status=validation.status,
            method=validation.method,
            campaign=validation.campaign,
        )

        if (
            alert_valid
            and validation.status == "valid"
            and TELEGRAM_BOT_TOKEN
            and TELEGRAM_CHAT_IDS
        ):
            alert = format_referral_alert(candidate, validation)
            try:
                successful, failures = await send_telegram_message_all(
                    TELEGRAM_BOT_TOKEN,
                    TELEGRAM_CHAT_IDS,
                    alert,
                )
                activity(
                    "telegram_delivery_completed",
                    referral_code=code,
                    successful=len(successful),
                    failed=len(failures),
                    level="ERROR" if failures else "INFO",
                )
            except Exception as exc:
                # Telegram should never be able to stop the discovery pipeline.
                activity(
                    "telegram_delivery_failed",
                    referral_code=code,
                    error_type=type(exc).__name__,
                    level="ERROR",
                )
        else:
            activity(
                "telegram_not_sent",
                referral_code=code,
                validation_status=validation.status,
                reason="only_confirmed_valid_referrals_are_sent",
            )

        results.append((candidate, validation, True))

    return results
