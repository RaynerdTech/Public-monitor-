from collections.abc import Awaitable, Callable

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from app.core.database import init_db, save_referral, update_validation
from app.core.extractor import extract_referral_code, extract_referral_links
from app.core.models import ReferralCandidate
from app.services.telegram import format_referral_alert, send_telegram_message
from app.services.validator import ValidationResult, validate_referral
from app.watchers.base import SourcePost


Validator = Callable[[str], Awaitable[ValidationResult]]


async def process_post(
    post: SourcePost,
    *,
    validator: Validator = validate_referral,
    alert_valid: bool = True,
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
            results.append((candidate, duplicate, False))
            continue

        validation = await validator(code)
        await update_validation(
            referral_code=code,
            status=validation.status,
            campaign=validation.campaign,
            validation_message=validation.message,
        )

        if (
            alert_valid
            and validation.status == "valid"
            and TELEGRAM_BOT_TOKEN
            and TELEGRAM_CHAT_ID
        ):
            alert = format_referral_alert(candidate, validation)
            try:
                await send_telegram_message(
                    TELEGRAM_BOT_TOKEN,
                    TELEGRAM_CHAT_ID,
                    alert,
                )
            except Exception:
                # Telegram should never be able to stop the discovery pipeline.
                pass

        results.append((candidate, validation, True))

    return results
