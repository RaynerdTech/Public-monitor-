Referral Monitor v9.3 - Render hosting update

Changes:
- Threads hosted OAuth now auto-detects Render's RENDER_EXTERNAL_URL / RENDER_EXTERNAL_HOSTNAME.
- Keeps explicit THREADS_REDIRECT_URI as highest priority.
- Keeps Railway domain support as a backwards-compatible fallback.
- Uses Render's PORT automatically (existing generic PORT behavior retained).
- Fixes the Threads OAuth local authorization URL call introduced during v9.2.
- Fixes threads-test so ThreadsWatcher receives only the token + queries it expects.
- Fixes watch-threads so it uses the stored OAuth token instead of requiring THREADS_ACCESS_TOKEN in .env.
- Adds Render-specific deployment notes and an optional render.yaml Blueprint.

Recommended Render web service:
Build command: pip install -r requirements.txt
Start command: python -m app.main threads-web-server
Health check path: /health

Recommended paid Render persistence:
Disk mount: /var/data
THREADS_TOKEN_FILE=/var/data/.threads-token.json
DATABASE_PATH=/var/data/referrals.db
PODCAST_SOURCE_REGISTRY=/var/data/podcast_sources.json
VALIDATOR_BROWSER_PROFILE_DIR=/var/data/browser-profile

The Threads callback URL is resolved automatically on Render as:
https://<your-service>.onrender.com/threads/callback

You can still set THREADS_REDIRECT_URI explicitly if you later use a custom domain.
