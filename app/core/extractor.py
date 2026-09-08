import re
from urllib.parse import urlparse


CLAUDE_REFERRAL_PATTERN = re.compile(
    r"https?://(?:www\.)?claude\.ai/referral/[A-Za-z0-9_-]+(?:\?[^\s<>'\"]*)?",
    re.IGNORECASE,
)


def extract_referral_links(text: str) -> list[str]:
    if not text:
        return []

    matches = CLAUDE_REFERRAL_PATTERN.findall(text)
    cleaned: list[str] = []
    seen: set[str] = set()

    for link in matches:
        link = link.rstrip(".,);]}>\"'")
        if link not in seen:
            seen.add(link)
            cleaned.append(link)

    return cleaned


def extract_referral_code(url: str) -> str | None:
    parsed = urlparse(url)

    if parsed.netloc.lower() not in {"claude.ai", "www.claude.ai"}:
        return None

    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or parts[0].lower() != "referral":
        return None

    return parts[1] or None
