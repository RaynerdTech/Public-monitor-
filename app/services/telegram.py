from datetime import datetime, timezone

import httpx

from app.core.models import ReferralCandidate
from app.services.validator import ValidationResult


async def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()


async def send_telegram_message_all(
    bot_token: str, chat_ids: list[str], text: str
) -> tuple[list[str], dict[str, str]]:
    """Send one message to every configured Telegram destination.

    Returns (successful_chat_ids, failures_by_chat_id). One broken destination
    must not prevent the others from receiving an alert.
    """
    successful: list[str] = []
    failures: dict[str, str] = {}
    for chat_id in chat_ids:
        try:
            await send_telegram_message(bot_token, chat_id, text)
            successful.append(chat_id)
        except Exception as exc:
            failures[chat_id] = type(exc).__name__
    return successful, failures


async def get_telegram_updates(bot_token: str) -> list[dict]:
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    if not data.get("ok"):
        raise RuntimeError("Telegram returned an unsuccessful response")
    return data.get("result", [])


def format_referral_alert(
    candidate: ReferralCandidate,
    result: ValidationResult,
    *,
    simulated: bool = False,
) -> str:
    now = datetime.now(timezone.utc)
    delay = None
    if candidate.post_created_at:
        posted = candidate.post_created_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        delay = max(0, int((candidate.detected_at - posted).total_seconds()))

    lines = []
    if simulated:
        lines.append("TEST ALERT - simulated validation")
    else:
        lines.append("New valid Claude referral")

    lines.extend(
        [
            f"Referral: {candidate.referral_url}",
            f"Source: {candidate.source}",
            f"Source URL: {candidate.source_url or '-'}",
            f"Status: {result.status}",
            f"Campaign: {result.campaign or '-'}",
        ]
    )

    if candidate.post_created_at:
        lines.append(f"Posted: {candidate.post_created_at.isoformat()}")
    lines.append(f"Detected: {candidate.detected_at.isoformat()}")
    if delay is not None:
        lines.append(f"Detection delay: {delay}s")
    lines.append(f"Alert generated: {now.isoformat()}")

    return "\n".join(lines)
