REFERRAL MONITOR v10.8.0 - ZERO-RESULT ROOT CAUSE FIX
=====================================================

Diagnosis-first release. No provider was replaced, no working integration was
redesigned. X filtered stream, Telegram alerts/buttons, Apify key rotation,
Scrape Creators credit tracking, Apify credit tracking, credit alerts,
deduplication, Podcast/RSS, Render deployment config and the database are all
byte-identical to v10.7.2.


1. THE SHARED ROOT CAUSE (Threads, Reddit, Facebook, Instagram)
---------------------------------------------------------------
Every social watcher applied its freshness window BEFORE checking whether the
row contained a referral link:

    if post is None or not is_recent_timestamp(post.created_at, lookback):
        continue                       # <- dropped here
    recent_results += 1
    if extract_referral_links(post.text):
        referral_results += 1          # <- only counted here

Two consequences:

  a) is_recent_timestamp(None, ...) returns False, so any row whose timestamp
     field we failed to map was discarded even when it demonstrably contained
     claude.ai/referral/.
  b) referral_results was only incremented after the gate, so the logs reported
     "referral_results: 0" for polls that really had found a referral link and
     then thrown it away. The failure was invisible.

FIX: new module app/watchers/candidates.py inverts the order.
  - The referral link is extracted first.
  - Rows with no link are rejected immediately (link-first, as required).
  - A row WITH a link but no usable timestamp is now accepted. Referral codes
    are de-duplicated in the database, so this cannot spam.
  - Freshness became a staleness ceiling (*_MAX_POST_AGE_MINUTES) that is
    deliberately decoupled from the polling lookback.
  - Every rejection records an explicit reason, and any rejected row that DID
    contain a referral link is always logged in full at WARNING level.


2. PER-SOURCE ROOT CAUSES
-------------------------

THREADS - raw_results: 0
  Payload parsing was correct (verified against ScrapeCreators' documented
  response: posts[] / taken_at / caption.text). The query was the fault.
  config._link_focused_queries discarded the keyword queries and sent only the
  literal string "claude.ai/referral". Threads keyword search cannot match a URL
  literal, so it returned nothing.
  FIX: Threads is queried with keywords, then results are strictly filtered for
  claude.ai/referral/. start_date/end_date now span the day boundary, trim is
  sent as the documented string "false", and an unexpected response envelope is
  logged instead of silently reporting zero.

REDDIT - raw_results: 7, recent_results: 0
  Not a parsing fault. timeframe=day returns the newest matching posts of the
  whole day while REDDIT_LOOKBACK_MINUTES=7 rejected anything older than seven
  minutes. For a query this rare the combination is arithmetically guaranteed to
  yield zero.
  FIX: REDDIT_MAX_POST_AGE_MINUTES=180 accepts a post the platform surfaced late
  while still rejecting genuinely old posts. The URL query is kept because
  Reddit's search does index link targets. parse_reddit_timestamp now also
  accepts the ISO created_at_iso field.

FACEBOOK - raw_results: 4, recent_results: 0
  Two faults, both confirmed against the Actor's published input schema.
  (i)  memo23~facebook-search-scraper accepts exactly searchType, searchQueries,
       maxItems, pageDelayMs, proxy. The code sent "onlyPostsNewerThan", which
       belongs to a different Actor and was silently ignored.
  (ii) It is a SEARCH RESULTS scraper and exposes relative times such as
       "2 minutes ago", not the publishedAt/timestamp/createdAt keys the parser
       looked for. created_at was therefore always None and every row died at
       the freshness gate - including the controlled TEST-FB-123 post.
  FIX: invalid input key removed; timestamp mapping widened to 14 key names plus
  relative-time and millisecond-epoch parsing; local date filtering only.

INSTAGRAM - raw_results: 0
  Input field names were correct. The query was the fault: the Actor matches on
  whole-word boundaries and its docs explicitly warn against punctuation, so
  "claude.ai/referral" matched nothing.
  FIX: keyword query plus strict post-filtering; the date window spans the day
  boundary; a run that returns zero rows now logs provider_returned_no_rows so a
  query problem is distinguishable from a filtering problem.

DIRECT WEB - sitemaps_polled: 7, pages_fetched: 0
  _get() cached ETag/Last-Modified for every URL including sitemaps and then
  "continue"d on 304, so a cached sitemap counted as polled while contributing
  zero page candidates. Compounding it, WEB_DIRECT_LOOKBACK_MINUTES defaulted to
  5, but sitemap <lastmod> is the page's modification time on a document that is
  commonly cached for longer, so a new post was already "too old" by the time the
  regenerated sitemap was served.
  FIX: discovery documents (feeds and sitemaps) are fetched unconditionally;
  conditional requests are kept for the much larger page bodies where they
  actually save bandwidth. Default lookback raised to 120 minutes. Full
  stage-by-stage counters added.

YOUTUBE - 0 candidates
  No logic fault found. The default query was a single string containing a bare
  slashed URL token, which parses poorly.
  FIX: quoted OR terms, plus a youtube_search_completed log line carrying
  items_returned and totalResults so "search returned nothing" is now
  distinguishable from "de-duplication removed everything".

WEB / EXA
  Results without a publishedDate were hard-rejected before the page was ever
  read. Forums and link aggregators - exactly where referral links get posted -
  frequently expose no publishedDate.
  FIX: undated results are inspected; only their content decides. Nothing is
  sent unless a real claude.ai/referral/ link is found. Results Exa itself dates
  outside the window are still rejected up front.

PODCAST / RSS - NOT BROKEN
  Audited, no change made. It already extracts referral links FIRST, then
  applies freshness, and logs an explicit reason for every rejection. It is the
  correct pattern and the one the social watchers deviated from. Its lack of
  recent hits reflects how rare referral links are in podcast metadata, not a
  defect.


3. NEW DIAGNOSTICS
------------------
source_poll_started now carries the active queries, lookback and max post age.
source_poll_completed now carries, per source:

  raw_results               rows the provider returned
  rows_with_referral_link   rows that actually contained claude.ai/referral/
  accepted                  rows passed to the pipeline
  rejected_no_link          rejected for containing no referral link
  rejected_stale            rejected for exceeding max post age
  rejected_future           rejected for an implausible future timestamp
  rejected_unparsable       rows we could not map at all
  rejected_duplicate        already seen in this process
  newest_result_age_seconds age of the freshest row the provider returned
  oldest_result_age_seconds age of the stalest row the provider returned

newest_result_age_seconds is the key new signal: if a provider keeps returning
rows that are hours old, the query or the provider index is the problem, not our
filtering.

Additional events:
  source_row_inspected           bounded per-row sample (SOURCE_DIAGNOSTIC_SAMPLES)
  referral_candidate_rejected    ALWAYS emitted, WARNING, when a row containing a
                                 referral link is rejected - with the URL, the
                                 timestamp, the age, the links and a text snippet
  provider_payload_unexpected    response envelope did not match expectations
  provider_returned_no_rows      Actor run produced nothing
  web_direct_fetch_failed        a feed/sitemap/page fetch failed
  youtube_search_completed       items_returned and totalResults per query

Direct Web additionally reports endpoints_probed, child_sitemaps_seen,
page_candidates, dated_candidates, dated_rejected_too_old, undated_candidates,
undated_bootstrapped, undated_new, pages_selected, pages_fetched and
newest_sitemap_lastmod.


4. FILES CHANGED
----------------
  app/watchers/candidates.py         NEW - link-first evaluation + diagnostics
  app/config.py                      query builder, max-age settings, defaults
  app/main.py                        wire max_post_age through; version 10.8.0
  app/watchers/scrape_threads.py     link-first, date window, envelope check
  app/watchers/scrape_reddit.py      link-first, ISO timestamp fallback
  app/watchers/apify_facebook.py     actor input fix, timestamp mapping
  app/watchers/apify_instagram.py    link-first, date window, zero-row logging
  app/watchers/reddit.py             parse_reddit_timestamp accepts ISO
  app/watchers/sites.py              unconditional discovery fetch, counters
  app/watchers/web.py                undated results inspected, counters
  app/watchers/youtube.py            search-level diagnostics
  .env.example                       new settings documented
  tests/                             6 files updated, 1 new regression file

Test suite: 139 passed (was 115).


5. REQUIRED RENDER ENV CHANGES
------------------------------
If these variables are currently set in Render, they MUST be updated, because an
explicit environment value always overrides the new defaults:

  THREADS_QUERIES              -> claude referral
  INSTAGRAM_QUERIES            -> claude referral
  FACEBOOK_QUERIES             -> claude referral
  REDDIT_QUERIES               -> claude.ai/referral   (leave as-is)
  WEB_DIRECT_LOOKBACK_MINUTES  -> 120

If they are NOT set, delete nothing; the new defaults already apply.

Optional new variables (defaults shown, all safe to omit):

  THREADS_MAX_POST_AGE_MINUTES=180
  REDDIT_MAX_POST_AGE_MINUTES=180
  INSTAGRAM_MAX_POST_AGE_MINUTES=360
  FACEBOOK_MAX_POST_AGE_MINUTES=360
  SOURCE_DIAGNOSTIC_SAMPLES=3
  YOUTUBE_QUERIES="claude referral"|"claude guest pass"|"claude.ai/referral"

Credit usage is unchanged: still exactly one query per source per poll.
