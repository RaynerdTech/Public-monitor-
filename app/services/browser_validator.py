import asyncio
import os
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


def _is_hosted_runtime() -> bool:
    """Return True for hosted Linux deployments such as Render.

    Render exposes RENDER_EXTERNAL_URL / RENDER_EXTERNAL_HOSTNAME and PORT.  The
    PORT fallback also keeps this useful on other container hosts without
    changing local Windows behaviour.
    """
    return bool(
        os.getenv("RENDER_EXTERNAL_URL")
        or os.getenv("RENDER_EXTERNAL_HOSTNAME")
        or (os.getenv("PORT") and os.name != "nt")
    )


def _browser_headless() -> bool:
    # Hosted services do not have an interactive desktop/display.  Keep the
    # existing local setting on developer machines so a user can manually clear
    # a challenge when needed.
    return True if _is_hosted_runtime() else VALIDATOR_BROWSER_HEADLESS


def _profile_dir() -> Path:
    configured = Path(VALIDATOR_BROWSER_PROFILE_DIR).expanduser()
    if _is_hosted_runtime() and not configured.is_absolute():
        # /tmp is writable in Render/Docker.  A paid persistent disk can still
        # be used by setting VALIDATOR_BROWSER_PROFILE_DIR to an absolute path.
        configured = Path("/tmp") / configured
    return configured.resolve()


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
    profile_dir = _profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)

    hosted = _is_hosted_runtime()
    headless = _browser_headless()

    # Render's Docker image installs Playwright's bundled Chromium, not branded
    # Google Chrome/Edge.  Locally we keep the configured branded browser first
    # because that preserves the existing Windows workflow, then fall back to
    # Playwright Chromium if it is installed.
    channels: list[str | None] = []
    if hosted:
        channels.append(None)
    else:
        configured = VALIDATOR_BROWSER_CHANNEL.strip() if VALIDATOR_BROWSER_CHANNEL else ""
        if configured.lower() in {"chromium", "playwright", "bundled"}:
            channels.append(None)
        elif configured:
            channels.append(configured)
        for channel in ("chrome", "msedge", None):
            if channel not in channels:
                channels.append(channel)

    last_error: Exception | None = None
    for channel in channels:
        try:
            kwargs = {
                "user_data_dir": str(profile_dir),
                "headless": headless,
                "viewport": {"width": 1280, "height": 900},
                # Render's containers have a small /dev/shm allocation.  This
                # avoids Chromium crashes under the 512 MB free instance.
                "args": ["--disable-dev-shm-usage"],
            }
            if channel is not None:
                kwargs["channel"] = channel
            return await playwright.chromium.launch_persistent_context(**kwargs)
        except Exception as exc:  # pragma: no cover - runtime/browser specific
            last_error = exc

    if last_error:
        raise last_error
    raise RuntimeError("No supported Chromium browser was available")


async def fetch_referral_api_in_browser(code: str) -> BrowserFetchResult:
    """Fetch Claude's referral API from a real browser session.

    This is only used after the normal HTTP validator is challenged by
    Cloudflare.  The browser profile is reused while the process is alive, and
    can be placed on persistent storage through VALIDATOR_BROWSER_PROFILE_DIR.
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
                        # Challenge pages can keep loading while their JS runs.
                        pass

                    # Do not immediately fail in hosted/headless mode.  Some
                    # Cloudflare managed challenges resolve automatically after
                    # browser JavaScript/cookies complete.  We only classify it
                    # blocked if the challenge remains after the configured wait.
                    if await _challenge_present(page):
                        wait_seconds = max(5, VALIDATOR_BROWSER_CHALLENGE_WAIT_SECONDS)
                        if _is_hosted_runtime():
                            # Do not stall every source pipeline for two minutes
                            # on a server-side challenge that needs human action.
                            wait_seconds = min(wait_seconds, 30)
                        deadline = asyncio.get_running_loop().time() + wait_seconds
                        while asyncio.get_running_loop().time() < deadline:
                            await asyncio.sleep(1)
                            if not await _challenge_present(page):
                                break

                        if await _challenge_present(page):
                            mode = "hosted headless browser" if _browser_headless() else "browser"
                            return BrowserFetchResult(
                                status_code=403,
                                message=(
                                    f"Cloudflare challenge remained in the {mode} after "
                                    f"{wait_seconds}s."
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
        except Exception as exc:  # pragma: no cover - runtime/browser specific
            # Keep the concrete launch/runtime error in Render logs through the
            # ValidationResult message instead of returning an opaque "blocked".
            return BrowserFetchResult(
                status_code=0,
                message=f"Browser validation failed: {type(exc).__name__}: {exc}",
            )
