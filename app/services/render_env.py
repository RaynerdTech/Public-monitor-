from __future__ import annotations

from urllib.parse import quote

import httpx


RENDER_API_URL = "https://api.render.com/v1"


async def persist_render_env_var(
    *,
    api_key: str,
    service_id: str,
    env_var_key: str,
    value: str,
    trigger_deploy: bool = True,
    timeout_seconds: float = 20.0,
) -> None:
    """Update one Render environment variable and optionally queue a deploy-only release."""
    api_key = api_key.strip()
    service_id = service_id.strip()
    env_var_key = env_var_key.strip()
    if not api_key:
        raise ValueError("Render API key is required")
    if not service_id:
        raise ValueError("Render service ID is required")
    if not env_var_key:
        raise ValueError("Render environment variable name is required")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    service = quote(service_id, safe="")
    env_key = quote(env_var_key, safe="")

    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        response = await client.put(
            f"{RENDER_API_URL}/services/{service}/env-vars/{env_key}",
            headers=headers,
            json={"value": value},
        )
        response.raise_for_status()

        if trigger_deploy:
            response = await client.post(
                f"{RENDER_API_URL}/services/{service}/deploys",
                headers=headers,
                json={"deployMode": "deploy_only"},
            )
            response.raise_for_status()
