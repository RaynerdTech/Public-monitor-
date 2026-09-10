import asyncio
import json
from dataclasses import dataclass

import httpx

from app.config import (
    VALIDATION_RETRIES,
    VALIDATION_TIMEOUT_SECONDS,
    VALIDATOR_BROWSER_FALLBACK_ENABLED,
)


@dataclass
class ValidationResult:
    status: str
    campaign: str | None = None
    is_valid: bool | None = None
    message: str | None = None
    raw: dict | None = None
    method: str = "direct"


_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
}


def classify_referral_payload(data, *, method: str = "direct") -> ValidationResult:
    if data is None:
        return ValidationResult(
            status="not_found",
            message="Referral not found",
            method=method,
        )

    if not isinstance(data, dict):
        return ValidationResult(
            status="unknown",
            message="Unexpected response shape",
            method=method,
        )

    campaign = data.get("campaign")
    is_valid = data.get("is_valid")

    if campaign == "claude_code_guest_pass_a47c" and is_valid is True:
        status = "valid"
    elif campaign == "claude_invite_contest" and is_valid is True:
        status = "contest"
    elif is_valid is False:
        status = "inactive"
    elif is_valid is True:
        status = "valid"
    else:
        status = "unknown"

    return ValidationResult(
        status=status,
        campaign=campaign,
        is_valid=is_valid,
        raw=data,
        method=method,
    )


def _looks_like_cloudflare(status_code: int, text: str, content_type: str = "") -> bool:
    if status_code != 403:
        return False

    haystack = f"{content_type}\n{text}".lower()
    return any(
        marker in haystack
        for marker in (
            "just a moment",
            "challenges.cloudflare.com",
            "cf-chl-",
            "cloudflare ray id",
        )
    )


def _classify_http_response(
    status_code: int,
    text: str,
    content_type: str = "",
    *,
    method: str,
) -> ValidationResult:
    if _looks_like_cloudflare(status_code, text, content_type):
        return ValidationResult(
            status="blocked",
            message="Cloudflare browser challenge",
            method=method,
        )

    if status_code != 200:
        return ValidationResult(
            status="error",
            message=f"HTTP {status_code}" if status_code else "Validation request failed",
            method=method,
        )

    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return ValidationResult(
            status="error",
            message="Invalid JSON response",
            method=method,
        )

    return classify_referral_payload(data, method=method)


async def _validate_direct(code: str) -> ValidationResult:
    url = f"https://claude.ai/api/referral/code/{code}"
    last_error: str | None = None

    for attempt in range(VALIDATION_RETRIES):
        try:
            async with httpx.AsyncClient(
                timeout=VALIDATION_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers=_HEADERS,
            ) as client:
                response = await client.get(url)

            result = _classify_http_response(
                response.status_code,
                response.text,
                response.headers.get("content-type", ""),
                method="direct",
            )

            # Do not keep hammering Claude when Cloudflare has explicitly challenged us.
            if result.status == "blocked":
                return result
            if result.status != "error":
                return result

            last_error = result.message
        except httpx.HTTPError as exc:
            last_error = str(exc)

        if attempt + 1 < VALIDATION_RETRIES:
            await asyncio.sleep(0.75 * (attempt + 1))

    return ValidationResult(
        status="error",
        message=last_error or "Validation request failed",
        method="direct",
    )


async def validate_referral(code: str) -> ValidationResult:
    direct = await _validate_direct(code)
    if direct.status != "blocked" or not VALIDATOR_BROWSER_FALLBACK_ENABLED:
        return direct

    from app.services.browser_validator import fetch_referral_api_in_browser

    browser = await fetch_referral_api_in_browser(code)
    if browser.status_code == 0:
        return ValidationResult(
            status="blocked",
            message=browser.message or "Browser fallback could not run",
            method="browser",
        )
    if browser.status_code == 403 and browser.message:
        return ValidationResult(
            status="blocked",
            message=browser.message,
            method="browser",
        )

    result = _classify_http_response(
        browser.status_code,
        browser.text,
        browser.content_type,
        method="browser",
    )
    if result.status == "blocked" and browser.message:
        result.message = browser.message
    return result
