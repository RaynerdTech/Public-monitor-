from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import Any

import httpx

from app.core.activity_log import activity


APIFY_BASE_URL = "https://api.apify.com/v2"


class ApifyClient:
    """Minimal Apify REST client with a dynamic token provider for live rotation."""

    def __init__(
        self,
        token_provider: Callable[[], str],
        *,
        timeout_seconds: float = 150.0,
        max_run_cost_usd: float = 0.10,
        base_url: str = APIFY_BASE_URL,
    ) -> None:
        self.token_provider = token_provider
        self.timeout_seconds = max(15.0, float(timeout_seconds))
        self.max_run_cost_usd = max(0.01, float(max_run_cost_usd))
        self.base_url = base_url.rstrip("/")
        self.total_runs = 0

    def _token(self) -> str:
        token = self.token_provider().strip()
        if not token:
            raise ValueError("Apify API token is required")
        return token

    @staticmethod
    def _actor_id(actor_id: str) -> str:
        cleaned = actor_id.strip().strip("/")
        if not cleaned:
            raise ValueError("Apify actor ID is required")
        return cleaned.replace("/", "~")

    async def validate_token(self, token: str | None = None) -> dict[str, Any]:
        active_token = (token or self._token()).strip()
        headers = {"Authorization": f"Bearer {active_token}", "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            response = await client.get(f"{self.base_url}/users/me", headers=headers)
            response.raise_for_status()
            payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, dict) else {}

    async def run_actor(
        self,
        actor_id: str,
        actor_input: dict[str, Any],
        *,
        source: str,
        max_items: int,
    ) -> list[dict[str, Any]]:
        normalized_actor = self._actor_id(actor_id)
        token = self._token()
        url = f"{self.base_url}/actors/{normalized_actor}/run-sync-get-dataset-items"
        params = {
            "clean": "true",
            "maxItems": max(1, int(max_items)),
            "maxTotalChargeUsd": self.max_run_cost_usd,
            "timeout": min(300, max(15, int(self.timeout_seconds - 5))),
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        activity(
            "apify_actor_run_started",
            source=source,
            actor=normalized_actor,
            max_items=max(1, int(max_items)),
            max_total_charge_usd=self.max_run_cost_usd,
        )
        started = monotonic()
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
        ) as client:
            response = await client.post(
                url,
                params=params,
                headers=headers,
                json=actor_input,
            )

        self.total_runs += 1
        elapsed_ms = round((monotonic() - started) * 1000)

        if response.is_error:
            activity(
                "apify_actor_run_failed",
                source=source,
                actor=normalized_actor,
                http_status=response.status_code,
                duration_ms=elapsed_ms,
                level="ERROR",
            )
            response.raise_for_status()

        try:
            payload = response.json()
        except ValueError as exc:
            activity(
                "apify_actor_run_failed",
                source=source,
                actor=normalized_actor,
                error="invalid_json",
                duration_ms=elapsed_ms,
                level="ERROR",
            )
            raise RuntimeError("Apify returned invalid JSON") from exc

        if not isinstance(payload, list):
            raise RuntimeError("Apify Actor did not return a dataset item list")

        rows = [row for row in payload if isinstance(row, dict)]
        activity(
            "apify_actor_run_completed",
            source=source,
            actor=normalized_actor,
            duration_ms=elapsed_ms,
            results=len(rows),
        )
        return rows
