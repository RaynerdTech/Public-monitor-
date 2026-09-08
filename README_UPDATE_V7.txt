Referral Monitor v7 update

Changed/new files only.

What changed:
- Added YouTube Data API v3 watcher for newly published public videos.
- Added youtube-test command to verify the API key and search access.
- Added watch-youtube command for continuous monitoring.
- Fetches full video descriptions before referral extraction so links in descriptions are not missed.
- Uses title + full description, then sends matches through the existing validator, dedupe, database and Telegram pipeline.
- Records the YouTube watch URL and video publish time as the source metadata.
- Uses a recent publishedAfter window plus overlap and video-ID dedupe to reduce repeat processing.
- Added YouTube configuration to status and .env.example.
- Fixed the accidental extra Reddit arguments in telegram-test from the previous source snapshot.
- No new Python package is required; this uses the existing httpx dependency.

YouTube quota note (current API model):
- search.list has its own default bucket of 100 calls/day.
- The default watcher interval is 960 seconds (~16 minutes), which is about 90 search calls/day for one configured query and leaves room for manual tests.
- Keep the default YOUTUBE_QUERIES as one OR-style query unless you have more search quota.
- videos.list is used for full descriptions and is low-cost compared with the separate search bucket.
- Broad YouTube search can still have indexing delay. Later we can directly monitor useful recurring channels through their uploads feeds for faster/cheaper detection.

After applying:
  pytest
  python -m app.main status

With YOUTUBE_API_KEY already in .env:
  python -m app.main youtube-test
  python -m app.main watch-youtube

Expected status:
  YouTube                 yes
  YouTube interval        960s

Production behavior:
- A video can match the YouTube search without containing an actual Claude referral URL.
- Only an extracted referral URL enters the validator.
- Only valid referrals are sent to the normal Telegram production alert path.
