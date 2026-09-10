import asyncio
from dataclasses import dataclass
from pathlib import Path

from app.config import (
    VALIDATOR_BROWSER_CHANNEL,
    VALIDATOR_BROWSER_CHALLENGE_WAIT_SECONDS,
    VALIDATOR_BROWSER_HEADLESS,
    VALIDATOR_BROWSER_NAVIGATION_TIMEOUT_SECONDS,
    VALIDATOR_BROWSER_PROFILE_DIR,
)


@dataclass
class BrowserFetchResult:
    status_code: int
    text: str = ""
    content_type: str = ""
    message: str | None = None


_BROWSER_LOCK = asyncio.Lock()


async def _challenge_present(page) -> bool:
    try:
        title = (await page.title()).strip().lower()
    except Exception:
        title = ""

    if "just a moment" in title or "attention required" in title:
        return True

    try:
        html = (await page.content()).lower()
    except Exception:
        return False

    return (
        "challenges.cloudflare.com" in html
        or "cf-chl-" in html
        or "cloudflare ray id" in html
    )


async def _launch_persistent_context(playwright):
    profile_dir = Path(VALIDATOR_BROWSER_PROFILE_DIR).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    channels: list[str | None] = []
    if VALIDATOR_BROWSER_CHANNEL:
        channels.append(VALIDATOR_BROWSER_CHANNEL)
    for channel in ("chrome", "msedge"):
        if channel not in channels:
            channels.append(channel)

    last_error: Exception | None = None
    for channel in channels:
        try:
            return await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel=channel,
                headless=VALIDATOR_BROWSER_HEADLESS,
                viewport={"width": 1280, "height": 900},
            )
        except Exception as exc:  # pragma: no cover - depends on local browsers
            last_error = exc

    if last_error:
        raise last_error
    raise RuntimeError("No supported Chromium browser was available")


async def fetch_referral_api_in_browser(code: str) -> BrowserFetchResult:
    """Fetch Claude's referral API from a real browser session.

    This is only used after the normal HTTP validator is challenged by Cloudflare.
    The profile is persistent so a normal Cloudflare clearance can be reused later.
    """

    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError:
        return BrowserFetchResult(
            status_code=0,
            message=(
                "Browser fallback is enabled but Playwright is not installed. "
                "Run: python -m pip install -r requirements.txt"
            ),
        )

    referral_url = f"https://claude.ai/referral/{code}"
    api_url = f"https://claude.ai/api/referral/code/{code}"

    async with _BROWSER_LOCK:
        try:
            async with async_playwright() as playwright:
                context = await _launch_persistent_context(playwright)
                try:
                    page = context.pages[0] if context.pages else await context.new_page()
                    page.set_default_timeout(
                        int(VALIDATOR_BROWSER_NAVIGATION_TIMEOUT_SECONDS * 1000)
                    )

                    try:
                        await page.goto(
                            referral_url,
                            wait_until="domcontentloaded",
                            timeout=int(VALIDATOR_BROWSER_NAVIGATION_TIMEOUT_SECONDS * 1000),
                        )
                    except PlaywrightTimeoutError:
                        # A challenge page can keep loading while the user completes it.
                        pass

                    if await _challenge_present(page):
                        if VALIDATOR_BROWSER_HEADLESS:
                            return BrowserFetchResult(
                                status_code=403,
                                message=(
                                    "Cloudflare challenge is still present in headless mode. "
                                    "Use headed browser fallback for initial clearance."
                                ),
                            )

                        deadline = asyncio.get_running_loop().time() + max(
                            5, VALIDATOR_BROWSER_CHALLENGE_WAIT_SECONDS
                        )
                        while asyncio.get_running_loop().time() < deadline:
                            if not await _challenge_present(page):
                                break
                            await asyncio.sleep(1)

                        if await _challenge_present(page):
                            return BrowserFetchResult(
                                status_code=403,
                                message=(
                                    "Cloudflare challenge was not cleared before the browser "
                                    "validation timeout."
                                ),
                            )

                    result = await page.evaluate(
                        """
                        async (url) => {
                          try {
                            const response = await fetch(url, {
                              method: 'GET',
                              credentials: 'include',
                              headers: {
                                'Accept': 'application/json, text/plain, */*'
                              }
                            });
                            return {
                              status: response.status,
                              contentType: response.headers.get('content-type') || '',
                              text: await response.text()
                            };
                          } catch (error) {
                            return {
                              status: 0,
                              contentType: '',
                              text: '',
                              error: String(error)
                            };
                          }
                        }
                        """,
                        api_url,
                    )

                    return BrowserFetchResult(
                        status_code=int(result.get("status") or 0),
                        text=str(result.get("text") or ""),
                        content_type=str(result.get("contentType") or ""),
                        message=str(result.get("error")) if result.get("error") else None,
                    )
                finally:
                    await context.close()
        except Exception as exc:  # pragma: no cover - local browser/runtime specific
            return BrowserFetchResult(
                status_code=0,
                message=f"Browser validation failed: {exc}",
            )
