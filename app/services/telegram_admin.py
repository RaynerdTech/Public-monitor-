from __future__ import annotations

import httpx

from app.config import (
    APIFY_MAX_RUN_COST_USD,
    APIFY_RENDER_AUTO_DEPLOY,
    RENDER_API_KEY,
    RENDER_SERVICE_ID,
    TELEGRAM_ADMIN_CHAT_IDS,
    TELEGRAM_BOT_TOKEN,
)
from app.core.activity_log import activity
from app.services.apify import ApifyClient
from app.services.render_env import persist_render_env_var
from app.services.runtime_secrets import get_apify_token, masked_apify_token, set_apify_token
from app.services.telegram import delete_telegram_message, send_telegram_message


def parse_apify_token_command(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    head, *rest = stripped.split(maxsplit=1)
    command = head.split("@", 1)[0].lower()
    if command != "/set_apify_token":
        return None
    return rest[0].strip() if rest else ""


def is_admin_chat(chat_id: str) -> bool:
    return bool(chat_id and chat_id in TELEGRAM_ADMIN_CHAT_IDS)


async def _validate_apify_token(token: str) -> dict:
    client = ApifyClient(
        lambda: token,
        timeout_seconds=30,
        max_run_cost_usd=APIFY_MAX_RUN_COST_USD,
    )
    return await client.validate_token(token)


async def handle_telegram_admin_message(message: dict) -> bool:
    """Handle Telegram admin commands. Returns True when the message was a known command."""
    if not TELEGRAM_BOT_TOKEN:
        return False

    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    chat_id = str(chat.get("id") or "")
    message_id = message.get("message_id")
    text = message.get("text")

    if isinstance(text, str) and text.strip().split(maxsplit=1)[0].split("@", 1)[0].lower() == "/whoami":
        await send_telegram_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            f"Your Telegram chat ID is: {chat_id}",
        )
        return True

    if isinstance(text, str) and text.strip().split(maxsplit=1)[0].split("@", 1)[0].lower() == "/apify_status":
        if not is_admin_chat(chat_id):
            await send_telegram_message(TELEGRAM_BOT_TOKEN, chat_id, "Not authorized for admin commands.")
            return True
        durable = bool(RENDER_API_KEY and RENDER_SERVICE_ID)
        await send_telegram_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            "Apify key: "
            + masked_apify_token()
            + ("\nRender rotation: ready" if durable else "\nRender rotation: not configured"),
        )
        return True

    token = parse_apify_token_command(text)
    if token is None:
        return False

    if not is_admin_chat(chat_id):
        await send_telegram_message(TELEGRAM_BOT_TOKEN, chat_id, "Not authorized for key rotation.")
        activity("telegram_apify_rotation_rejected", destination=chat_id, reason="unauthorized_chat", level="WARNING")
        return True

    # Remove the message containing the secret as soon as possible. Telegram may
    # refuse deletion in some chats; failure must not block rotation.
    if isinstance(message_id, int):
        try:
            await delete_telegram_message(TELEGRAM_BOT_TOKEN, chat_id, message_id)
        except Exception:
            pass

    if not token:
        await send_telegram_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            "Usage: /set_apify_token YOUR_NEW_APIFY_TOKEN",
        )
        return True

    if not token.startswith("apify_api_"):
        await send_telegram_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            "That does not look like an Apify API token. Nothing changed.",
        )
        return True

    try:
        account = await _validate_apify_token(token)
    except httpx.HTTPStatusError as exc:
        await send_telegram_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            f"Apify rejected that key (HTTP {exc.response.status_code}). Nothing changed.",
        )
        activity("telegram_apify_rotation_failed", destination=chat_id, reason="invalid_apify_token", level="ERROR")
        return True
    except Exception as exc:
        await send_telegram_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            f"Could not verify the Apify key ({type(exc).__name__}). Nothing changed.",
        )
        activity("telegram_apify_rotation_failed", destination=chat_id, reason=type(exc).__name__, level="ERROR")
        return True

    # Switch the currently running watchers immediately. They read the token on
    # every Actor call rather than only at process startup.
    set_apify_token(token)

    username = str(account.get("username") or "Apify account")
    if not (RENDER_API_KEY and RENDER_SERVICE_ID):
        await send_telegram_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            f"✅ Apify key verified for {username} and activated now.\n"
            "⚠️ Render API credentials are not configured, so the key will be lost on restart.",
        )
        activity("telegram_apify_rotation_completed", destination=chat_id, durable=False, deploy_queued=False)
        return True

    try:
        await persist_render_env_var(
            api_key=RENDER_API_KEY,
            service_id=RENDER_SERVICE_ID,
            env_var_key="APIFY_API_TOKEN",
            value=token,
            trigger_deploy=APIFY_RENDER_AUTO_DEPLOY,
        )
    except Exception as exc:
        await send_telegram_message(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            f"✅ Apify key verified for {username} and activated now.\n"
            f"⚠️ Render could not save/deploy it ({type(exc).__name__}).",
        )
        activity(
            "telegram_apify_rotation_completed",
            destination=chat_id,
            durable=False,
            deploy_queued=False,
            render_error=type(exc).__name__,
            level="ERROR",
        )
        return True

    await send_telegram_message(
        TELEGRAM_BOT_TOKEN,
        chat_id,
        f"✅ Apify key updated for {username}.\n"
        "The running monitor switched immediately.\n"
        + ("Render deploy queued." if APIFY_RENDER_AUTO_DEPLOY else "Saved to Render; auto-deploy is off."),
    )
    activity(
        "telegram_apify_rotation_completed",
        destination=chat_id,
        durable=True,
        deploy_queued=APIFY_RENDER_AUTO_DEPLOY,
    )
    return True
