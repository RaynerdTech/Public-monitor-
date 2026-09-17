Referral Monitor v10.7 — link-first discovery + full credit/quota status

What changed
- Paid social discovery now searches the actual referral domain `claude.ai/referral` by default for Threads, Reddit, Facebook, and Instagram, even if old Render ENV values still say `claude referral`.
- Set SEARCH_INCLUDE_BROAD_KEYWORDS=true only if you also want the legacy keyword searches. This adds extra paid searches, so the default is false.
- YouTube keeps the referral domain plus keyword fallbacks in one OR-style search query.
- Exa defaults to the exact referral URL prefix.
- Podcast discovery now includes `claude.ai/referral`; RSS/transcript parsing already extracts the actual referral link.
- Facebook, Instagram, and Reddit now also extract referral URLs from nested API/Actor fields, not only the visible caption/body.
- Source logs now expose raw/recent/referral counts for Threads, Reddit, Facebook, Instagram, and YouTube so `posts_found: 0` is diagnosable.
- `/credits` now reports all services: Scrape Creators, Apify, X usage/credit (when X exposes it), YouTube quota estimate, Exa runtime usage/cost estimate, Podcast Index, direct web, and Telegram.
- Exa and YouTube runtime API usage is counted since process restart.
- Fixed the broken `python -m app.main web-test` constructor regression.
- APIFY_RENDER_AUTO_DEPLOY remains false.

New/important ENV
REFERRAL_SEARCH_TERM=claude.ai/referral
SEARCH_INCLUDE_BROAD_KEYWORDS=false
YOUTUBE_DAILY_SEARCH_QUOTA=100
EXA_SEARCH_PRICE_USD=0.007

Current intended paid-social intervals in the update defaults
THREADS_WATCH_INTERVAL_SECONDS=300
REDDIT_WATCH_INTERVAL_SECONDS=300
FACEBOOK_WATCH_INTERVAL_SECONDS=300
INSTAGRAM_WATCH_INTERVAL_SECONDS=300

Notes
- `SEARCH_INCLUDE_BROAD_KEYWORDS=false` avoids doubling Scrape Creators / Apify requests. The exact referral-domain query is the primary discovery strategy.
- X already uses `url:"claude.ai/referral"` and is unchanged.
- YouTube does not expose live remaining search quota through the normal Data API; `/credits` reports configured quota and runtime calls and tells you to use Google Cloud Console for the live balance.
- Exa's documented Search API does not expose account balance; `/credits` reports runtime requests/cost and points to the Exa dashboard for the live balance.
- Podcast Index's core index is free; there is no paid credit balance to show.
