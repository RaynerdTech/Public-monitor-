from datetime import datetime, timezone

import httpx

from app.core.models import ReferralCandidate
from app.services.validator import ValidationResult


async def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    *,
    reply_markup: dict | None = None,
) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()


async def send_telegram_message_all(
    bot_token: str,
    chat_ids: list[str],
    text: str,
    *,
    reply_markup: dict | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Send one message to every configured Telegram destination.

    Returns (successful_chat_ids, failures_by_chat_id). One broken destination
    must not prevent the others from receiving an alert.
    """
    successful: list[str] = []
    failures: dict[str, str] = {}
    for chat_id in chat_ids:
        try:
            await send_telegram_message(
                bot_token,
                chat_id,
                text,
                reply_markup=reply_markup,
            )
            successful.append(chat_id)
        except Exception as exc:
            failures[chat_id] = type(exc).__name__
    return successful, failures


async def get_telegram_updates(
    bot_token: str,
    *,
    offset: int | None = None,
    timeout: int = 0,
) -> list[dict]:
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params: dict[str, int | str] = {
        "timeout": max(0, timeout),
        "allowed_updates": '["callback_query"]',
    }
    if offset is not None:
        params["offset"] = offset
    async with httpx.AsyncClient(timeout=max(10, timeout + 5)) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    if not data.get("ok"):
        raise RuntimeError("Telegram returned an unsuccessful response")
    return data.get("result", [])


async def answer_telegram_callback(
    bot_token: str,
    callback_query_id: str,
    text: str,
) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            url,
            json={"callback_query_id": callback_query_id, "text": text},
        )
        response.raise_for_status()


async def edit_telegram_message(
    bot_token: str,
    chat_id: str,
    message_id: int,
    text: str,
    *,
    reply_markup: dict | None = None,
) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()


def format_referral_keyboard(candidate: ReferralCandidate) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "Open referral now", "url": candidate.referral_url}],
            [
                {
                    "text": "Worked",
                    "callback_data": f"referral_feedback:worked:{candidate.referral_code}",
                },
                {
                    "text": "Already used / invalid",
                    "callback_data": f"referral_feedback:unusable:{candidate.referral_code}",
                },
            ],
        ]
    }


def parse_referral_feedback(data: object) -> tuple[str, str] | None:
    if not isinstance(data, str):
        return None
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "referral_feedback":
        return None
    status, code = parts[1], parts[2]
    if status not in {"worked", "unusable"} or not code:
        return None
    return status, code


def format_feedback_result(text: str, status: str) -> str:
    lines = [line for line in text.splitlines() if not line.startswith("Claim result:")]
    label = "WORKED" if status == "worked" else "ALREADY USED / INVALID"
    lines.append(f"Claim result: {label} (reported in Telegram)")
    return "\n".join(lines)


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
    elif result.status == "valid":
        lines.append("NEW CLAUDE REFERRAL - API CHECK SAYS VALID")
    else:
        lines.append("NEW CLAUDE REFERRAL - OPEN NOW")

    status_labels = {
        "valid": "Valid",
        "inactive": "Inactive",
        "not_found": "Not found",
        "contest": "Contest link",
        "blocked": "Could not verify automatically - open manually",
        "error": "Could not verify automatically - open manually",
        "unknown": "Could not verify automatically - open manually",
        "pending": "Not checked yet - open manually",
    }

    lines.extend(
        [
            f"Referral: {candidate.referral_url}",
            f"Source: {candidate.source}",
            f"Source URL: {candidate.source_url or '-'}",
            f"Automatic check: {status_labels.get(result.status, result.status)}",
            f"Campaign: {result.campaign or '-'}",
        ]
    )

    if candidate.post_created_at:
        lines.append(f"Posted: {candidate.post_created_at.isoformat()}")
    lines.append(f"Detected: {candidate.detected_at.isoformat()}")
    if delay is not None:
        lines.append(f"Detection delay: {delay}s")
    lines.append(f"Alert generated: {now.isoformat()}")
    if result.status != "valid":
        lines.append("The automatic check does not delay or decide delivery.")

    return "\n".join(lines)
