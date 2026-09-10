Referral Monitor v8 - broad web/blog/forum/news discovery with Exa

What changed
- Added Exa Search as the first broad public-web discovery layer.
- Added web-test for one-off connectivity/search checks.
- Added watch-web for continuous automatic discovery.
- Exa finds candidate pages; the monitor then fetches candidate page HTML directly and passes any Claude referral URLs through the existing shared validator/dedupe/Telegram pipeline.
- Keeps undated pages instead of dropping them, because forum/community pages often do not expose a structured publication date.
- Published pages older than EXA_LOOKBACK_MINUTES are ignored.
- Search/API/network errors retry automatically instead of permanently stopping the watcher.
- Added Exa status rows.

Setup
1. Create an Exa API key at https://dashboard.exa.ai/api-keys
2. Add it to your local .env:
   EXA_API_KEY=your_key_here

Recommended initial settings
EXA_WATCH_INTERVAL_SECONDS=300
EXA_SEARCH_LIMIT=10
EXA_LOOKBACK_MINUTES=60
EXA_SEARCH_TYPE=fast
EXA_PAGE_FETCH_TIMEOUT_SECONDS=12
EXA_QUERIES=latest public pages posts blogs forums news containing claude.ai/referral or Claude guest pass

Five-minute polling with one search query is 288 searches/day. Exa Search is usage-based, so keep one combined query unless a second query proves necessary.

Test
python -m app.main web-test --lookback-minutes 10080

Continuous monitoring
python -m app.main watch-web

Only referrals that validate as status=valid are eligible for Telegram alerts. The same browser-backed Claude validation fallback used by X and YouTube is used here too.
