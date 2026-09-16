from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.activity_log import activity


SCRAPE_CREATORS_BASE_URL = "https://api.scrapecreators.com"


@dataclass(frozen=True)
class ScrapeCreatorsResponse:
    payload: dict[str, Any]
    credits_charged: int
    credits_remaining: int | None


class ScrapeCreatorsClient:
    """Small authenticated client shared by the social-source watchers."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 30.0,
        base_url: str = SCRAPE_CREATORS_BASE_URL,
    ) -> None:
        api_key = api_key.strip()
        if not api_key:
            raise ValueError("Scrape Creators API key is required")

        self.api_key = api_key
        self.timeout_seconds = max(3.0, float(timeout_seconds))
        self.base_url = base_url.rstrip("/")
        self.total_requests = 0
        self.total_credits_charged = 0
        self.last_credits_remaining: int | None = None

    @staticmethod
    def _as_int(value: object) -> int | None:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    async def get(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
        source: str,
    ) -> ScrapeCreatorsResponse:
        endpoint = path if path.startswith("/") else f"/{path}"
        url = f"{self.base_url}{endpoint}"

        activity(
            "scrape_creators_request_started",
            source=source,
            endpoint=endpoint,
        )

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers={"x-api-key": self.api_key},
        ) as client:
            response = await client.get(url, params=params)

        self.total_requests += 1

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if not isinstance(payload, dict):
            payload = {}

        charged = self._as_int(payload.get("credits_charged")) or 0
        remaining = self._as_int(payload.get("credits_remaining"))
        self.total_credits_charged += charged
        if remaining is not None:
            self.last_credits_remaining = remaining

        if response.is_error:
            activity(
                "scrape_creators_request_failed",
                source=source,
                endpoint=endpoint,
                http_status=response.status_code,
                credits_charged=charged,
                credits_remaining=remaining,
                level="ERROR",
            )
            response.raise_for_status()

        if payload.get("success") is False:
            message = str(payload.get("message") or payload.get("error") or "Scrape Creators request failed")
            activity(
                "scrape_creators_request_failed",
                source=source,
                endpoint=endpoint,
                error=message[:300],
                credits_charged=charged,
                credits_remaining=remaining,
                level="ERROR",
            )
            raise RuntimeError(message)

        activity(
            "scrape_creators_request_completed",
            source=source,
            endpoint=endpoint,
            credits_charged=charged,
            credits_remaining=remaining,
        )
        return ScrapeCreatorsResponse(
            payload=payload,
            credits_charged=charged,
            credits_remaining=remaining,
        )
