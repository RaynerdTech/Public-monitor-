Referral Monitor v3 update (changed/new files only)

What this adds:
- First real social-platform watcher: official Meta Threads keyword search
- RECENT Threads search mode for near-live discovery
- Multiple configurable Threads search queries
- Threads post deduplication by post ID
- Rate-limit/transient-error backoff
- `threads-test` command to verify the Meta token before continuous monitoring
- `watch-threads` command for 24/7 monitoring
- `status` command to show what is configured
- Threads watcher tests

Meta setup required for the live Threads watcher:
1. Create a Meta app with the Threads use case.
2. Authorize an access token that includes `threads_keyword_search`.
3. Put the token in `.env` as THREADS_ACCESS_TOKEN.

Recommended first settings:
THREADS_QUERIES=claude.ai/referral,claude referral
THREADS_WATCH_INTERVAL_SECONDS=30
THREADS_SEARCH_LIMIT=50

Test credentials:
  python -m app.main status
  python -m app.main threads-test

Start monitoring:
  python -m app.main watch-threads

If Telegram is configured, newly discovered referrals that validate as usable
will automatically be sent to the configured Telegram chat.
