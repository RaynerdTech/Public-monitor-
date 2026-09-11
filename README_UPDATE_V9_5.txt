Referral Monitor v9.5 - hosted browser validator / Render Docker update

What changed
- Adds a Dockerfile so Render can install Chromium plus all Playwright OS dependencies reliably.
- Pins Playwright to 1.62.0 for reproducible browser installs.
- Hosted Render runtime automatically uses Playwright's bundled Chromium in headless mode.
- Local Windows behaviour remains compatible with the existing headed Chrome fallback.
- Hosted Chromium uses --disable-dev-shm-usage for the 512 MB Render free instance.
- Cloudflare managed challenges are allowed time to auto-resolve in headless mode before being classified blocked.
- Browser launch/runtime errors now preserve the concrete exception in validation messages.

Render change required
After pushing v9.5, change the existing Render service runtime from Python to Docker:
Settings -> Build -> Source -> Edit -> select same GitHub repo/branch -> Runtime: Docker -> Deploy.
Keep Health Check Path: /health and all existing environment variables.
Dockerfile CMD already runs: python -m app.main watch-all

Important
This makes the browser validator available on Render. It does not guarantee that Cloudflare will allow every server-hosted browser session. If Claude leaves an interactive challenge in place, the validator correctly remains blocked rather than falsely reporting a referral as valid.
