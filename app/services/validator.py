import asyncio
from dataclasses import dataclass

import httpx

from app.config import VALIDATION_RETRIES, VALIDATION_TIMEOUT_SECONDS


@dataclass
class ValidationResult:
    status: str
    campaign: str | None = None
    is_valid: bool | None = None
    message: str | None = None
    raw: dict | None = None


_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
}


async def validate_referral(code: str) -> ValidationResult:
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

            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
            else:
                try:
                    data = response.json()
                except ValueError:
                    return ValidationResult(
                        status="error",
                        message="Invalid JSON response",
                    )

                if data is None:
                    return ValidationResult(
                        status="not_found",
                        message="Referral not found",
                    )

                if not isinstance(data, dict):
                    return ValidationResult(
                        status="unknown",
                        message="Unexpected response shape",
                    )

                campaign = data.get("campaign")
                is_valid = data.get("is_valid")

                if campaign == "claude_code_guest_pass_a47c" and is_valid is True:
                    status = "valid"
                elif campaign == "claude_invite_contest" and is_valid is True:
                    status = "contest"
                elif is_valid is False:
                    status = "expired"
                elif is_valid is True:
                    status = "valid"
                else:
                    status = "unknown"

                return ValidationResult(
                    status=status,
                    campaign=campaign,
                    is_valid=is_valid,
                    raw=data,
                )

        except httpx.HTTPError as exc:
            last_error = str(exc)

        if attempt + 1 < VALIDATION_RETRIES:
            await asyncio.sleep(0.75 * (attempt + 1))

    return ValidationResult(
        status="error",
        message=last_error or "Validation request failed",
    )
