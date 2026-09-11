Referral Monitor v9.6 - Render/Playwright Docker hardening

Why this update exists:
- Render logs showed Playwright looking under /opt/render/.cache/ms-playwright, which means the running service was not using the browser location baked into the previous Dockerfile.
- The repository render.yaml still declared runtime: python, which conflicted with the intended Docker deployment.
- This update uses Playwright's official Python v1.62.0 Docker image, matching requirements.txt (playwright==1.62.0), and pins PLAYWRIGHT_BROWSERS_PATH=/ms-playwright.

After applying and pushing:
1. Render Settings > Build > Source > Edit: Runtime = Docker; click Deploy.
2. Manual Deploy > Clear build cache & deploy.
3. In the build logs, confirm the image build references mcr.microsoft.com/playwright/python:v1.62.0-noble.
4. In Environment, remove any user-defined PLAYWRIGHT_BROWSERS_PATH that points somewhere else. If present, set it to /ms-playwright.
5. Keep Health Check Path = /health.
