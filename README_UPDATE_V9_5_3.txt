Referral Monitor v9.5.3

Fixes the Render/Docker Playwright browser path mismatch seen in hosted validator logs.

Why the previous build failed:
- Chromium was installed during the Docker build under one HOME/cache path.
- Render's runtime HOME caused Playwright to look under /opt/render/.cache/ms-playwright.
- Result: browser fallback reported that the Chromium executable did not exist.

What changed:
- PLAYWRIGHT_BROWSERS_PATH is pinned to /ms-playwright for both build and runtime.
- Chromium is installed into that shared path.
- Browser garbage collection is disabled for the image so the installed binary is retained.
- Build prints Playwright's installed-browser list for easier Render diagnostics.

No application logic or API settings were changed.
