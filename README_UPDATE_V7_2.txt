Referral Monitor v7.2 - YouTube resilience update

Changes:
- YouTube production polling default changed to 20 minutes (1200 seconds).
  With one configured OR query this is about 72 search.list calls/day, below the
  current default 100/day bucket.
- If the YouTube daily search quota is exhausted, watch-youtube now sleeps until
  after the next midnight Pacific Time quota reset and resumes automatically.
- Transient network/API failures now retry automatically instead of permanently
  killing the watcher. Auth/config errors retry slowly to avoid hammering quota.
- Added status messages so the terminal shows when the watcher is sleeping/retrying.

Important:
- YouTube does not provide a global keyword push stream like X Filtered Stream.
- YouTube PubSubHubbub push notifications are channel-specific. Once the system is
  deployed with a public webhook, useful channels can be subscribed for near-real-time
  upload/update notifications while broad discovery continues every 20 minutes.

Recommended .env:
YOUTUBE_WATCH_INTERVAL_SECONDS=1200
YOUTUBE_LOOKBACK_MINUTES=30
