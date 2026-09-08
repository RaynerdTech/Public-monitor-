Referral Monitor v6 update

Changed/new files only.

What changed:
- Added Reddit OAuth Data API watcher for recent public posts.
- Added app-only OAuth token handling and automatic token refresh.
- Reddit post title/body/destination URL all feed into the existing referral extractor.
- Added reddit-test command for API connectivity checks.
- Added watch-reddit command. Matching posts use the same validator, dedupe, database and Telegram pipeline as X.
- Added Reddit configuration to status and .env.example.
- No new Python package is required; this uses the existing httpx dependency.

Important:
- Reddit currently requires explicit approval for Data API access.
- This v6 Reddit watcher covers searchable public Reddit submissions. It does not claim to capture every Reddit comment globally.
- Keep credentials only in .env. Do not paste client secrets into chat/screenshots.

After applying:
  pytest
  python -m app.main status

After Reddit credentials are added to .env:
  python -m app.main reddit-test
  python -m app.main watch-reddit
