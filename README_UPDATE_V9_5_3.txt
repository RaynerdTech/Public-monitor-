Referral Monitor v9.5.3 hotfix

Fixes the hosted Playwright browser path mismatch seen on Render.

Problem:
- Playwright installed Chromium during the Docker build under the build user's home cache.
- At runtime Render looked for Chromium under a different home/cache path.
- Result: "Executable doesn't exist" and hosted browser validation stayed blocked.

Fix:
- Sets PLAYWRIGHT_BROWSERS_PATH=/ms-playwright in the Docker image.
- Installs Chromium into that shared fixed path.
- Keeps the same path available at runtime.
- Makes the browser directory readable/executable by the runtime user.

After applying, push to GitHub and redeploy the Render Docker service.
