Referral Monitor v10.4 - Scrape Creators social monitoring
============================================================

What changed
------------
The production social-source layer now supports Scrape Creators for:

- Threads keyword search
- Reddit search
- Instagram hashtag search + Reels keyword search
- Facebook public pages + public groups

The existing referral pipeline is unchanged:
source -> referral extraction -> dedupe/database -> Telegram -> deferred validation.

The previous official Threads/Reddit API code is still present for backwards
compatibility, but `watch-all` no longer depends on Meta Threads OAuth or Reddit
OAuth approval when Scrape Creators is configured.

Required secret
---------------
Add this locally to `.env` and in Render Environment:

SCRAPE_CREATORS_API_KEY=YOUR_PRIVATE_KEY

Never put the real API key in `.env.example`, GitHub, screenshots, or logs.

Budget-aligned polling defaults
-------------------------------
Threads:
  THREADS_WATCH_INTERVAL_SECONDS=300
  THREADS_QUERIES=claude referral

Reddit:
  REDDIT_WATCH_INTERVAL_SECONDS=300
  REDDIT_QUERIES=claude referral
  REDDIT_SCRAPE_FILTER=posts
  REDDIT_SCRAPE_TIMEFRAME=day

Instagram:
  INSTAGRAM_WATCH_INTERVAL_SECONDS=900
  INSTAGRAM_HASHTAGS=
  INSTAGRAM_REELS_QUERIES=claude referral
  INSTAGRAM_HASHTAG_DATE_POSTED=last-day
  INSTAGRAM_REELS_DATE_POSTED=last-week

Facebook:
  FACEBOOK_WATCH_INTERVAL_SECONDS=600
  FACEBOOK_SOURCES=

Multiple values are separated with ||.

Examples:
  THREADS_QUERIES=claude referral||claude guest pass
  INSTAGRAM_HASHTAGS=claudeai||claudecode
  FACEBOOK_SOURCES=page:https://www.facebook.com/example||group:https://www.facebook.com/groups/123456789

Credit guardrails
-----------------
The live watchers intentionally do NOT auto-paginate. This keeps API use predictable.

Each configured Threads query = 1 request per Threads poll.
Each configured Reddit query = 1 request per Reddit poll.
Each Instagram hashtag = 1 request per Instagram poll.
Each Instagram Reels query = 1 request per Instagram poll.
Each Facebook page/group = 1 request per Facebook poll.

The `status` command now shows estimated Scrape Creators requests over 30 days
based on the exact number of configured queries/sources and poll intervals.
Scrape Creators responses are also logged with credits charged/remaining when
the provider returns those fields.

Provider constraints accounted for
-----------------------------------
Threads keyword search returns only the public results made available by Threads
(up to about 10 at a time), so the watcher checks frequently and deduplicates.

Facebook page/group endpoints return only a small current batch (documented as
3 posts at a time), so Facebook is designed as polling of selected sources rather
than a global Facebook keyword search.

Instagram hashtag/Reels endpoints are Google-indexed searches. Reels date filters
start at last-week; hashtag search can use last-hour/last-day/etc.

No exact Instagram hashtag or Facebook source was guessed. Add the real sources
before enabling those parts in production.

Local tests
-----------
After updating the project and adding the Scrape Creators key to your private .env:

python -m app.main status
python -m app.main threads-test
python -m app.main reddit-test
python -m app.main instagram-test
python -m app.main facebook-test

Run the full monitor with:

python -m app.main watch-all

The test commands consume real Scrape Creators credits because they make live API
requests. Run them deliberately while using the 100 free-credit test balance.

Render
------
After deployment, add these secrets/values in Render Environment as needed:

SCRAPE_CREATORS_API_KEY          required
INSTAGRAM_HASHTAGS               optional until exact hashtag(s) chosen
FACEBOOK_SOURCES                 required to enable Facebook

The default Render blueprint already includes the budget-aligned poll intervals
and the default single Threads/Reddit/Reels query.

Version: 10.4
