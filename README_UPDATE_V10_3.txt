Referral Monitor v10.3 update

What changed
- Added direct website monitoring that does not wait for Exa/search indexing.
- Supports configured WordPress feeds and sitemaps, Shopify blog feeds and
  sitemaps, generic RSS/Atom feeds, and standard sitemap indexes.
- Recent sitemap pages are fetched and checked for Claude referral URLs.
- Feed and sitemap requests use ETag/Last-Modified caching when supported.
- Duplicate referral content from the same page is suppressed.
- Podcast transcript connection failures now retry automatically.
- Repeated transcript-host failures are summarized and rate-limited in logs;
  one unreachable transcript host no longer creates repeated warning lines.

Required Render environment value
WEB_DIRECT_SOURCES=https://raynerdtech.ng

Add more known publishing sites with ||, for example:
WEB_DIRECT_SOURCES=https://raynerdtech.ng||https://store.example.com||https://example.org/feed.xml

Recommended defaults
WEB_DIRECT_WATCH_INTERVAL_SECONDS=120
WEB_DIRECT_LOOKBACK_MINUTES=180
WEB_DIRECT_TIMEOUT_SECONDS=15
WEB_DIRECT_MAX_PAGES_PER_POLL=50
PODCAST_TRANSCRIPT_RETRIES=2
PODCAST_TRANSCRIPT_FAILURE_LOG_SECONDS=900

Test commands
python -m app.main direct-web-test
python -m app.main podcast-test
pytest

Important limitation
Direct monitoring works for sites listed in WEB_DIRECT_SOURCES. Exa remains the
broad discovery layer for the rest of the public web; no service can poll every
website on the internet continuously without a known-site list or search index.
