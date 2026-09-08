Referral Monitor v4 update

Adds official X API recent-search monitoring using app-only Bearer Token authentication.

New commands:
  python -m app.main x-test
  python -m app.main watch-x

Recommended first test:
  python -m app.main status
  python -m app.main x-test

The watcher uses GET https://api.x.com/2/tweets/search/recent and requests post timestamps,
author information, and source URLs. It keeps a per-query since_id in memory so subsequent
polls request only newer posts and also keeps an in-process post-ID dedupe set.

Default X polling interval: 20 seconds.
Default max results per query: 100.

Valid new Claude referrals continue through the existing pipeline:
  X -> extract -> deduplicate -> Claude validity check -> database -> Telegram alert

If X returns an API-credit/billing error, add credits in the X Developer Console and rerun x-test.
Do not paste the Bearer Token into chat or commit .env.
