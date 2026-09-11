from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def activity(event: str, *, level: str = "INFO", **fields: Any) -> None:
    """Write one structured, secret-safe activity line to hosted service logs."""
    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level.upper(),
        "event": event,
    }
    payload.update(
        {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
    )
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)
