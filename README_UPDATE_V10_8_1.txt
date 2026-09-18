REFERRAL MONITOR v10.8.1 - RECENCY SEARCH FINISH
================================================

This is a narrow follow-up to v10.8.0.

1. Facebook now defaults to silentflow~facebook-search-scraper.
   - Uses Facebook's recent-post search surface (`recent_posts: true`).
   - Uses start_date/end_date as a broad server-side date window.
   - Existing parser already understands SilentFlow's `message`, `timestamp`,
     `url`, and `author` fields.
   - The old memo23 input is still supported if you override the actor ID.

2. Instagram keeps the existing Boolean-search Actor but uses an explicit
   Boolean retrieval query: `claude AND referral`.
   - `strictBooleanSyntax: true` is sent so query mistakes fail visibly.
   - Exact Telegram acceptance is still link-first: a row must actually contain
     claude.ai/referral/.
   - Search coverage stays `efficient` by default to avoid silently increasing
     cost. If a controlled test still returns zero rows, try `comprehensive`
     before replacing the Actor.

3. render.yaml was stale and could re-introduce the old broken production
   values on a Blueprint sync/redeploy. It is now aligned with v10.8:
   - THREADS_QUERIES=claude referral
   - REDDIT_QUERIES=claude.ai/referral
   - INSTAGRAM_QUERIES=claude AND referral
   - FACEBOOK_QUERIES=claude referral
   - APIFY_FACEBOOK_ACTOR_ID=silentflow~facebook-search-scraper
   - *_MAX_POST_AGE_MINUTES explicitly set
   - WEB_DIRECT_LOOKBACK_MINUTES=120

No changes to X, Telegram delivery, database schema, validation, credit
tracking, Podcast, Exa, YouTube, or Scrape Creators client logic.
