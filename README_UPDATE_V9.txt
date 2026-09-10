Referral Monitor v9 - Podcasts / RSS

Added:
- Podcast Index authentication using the documented SHA-1 request header scheme.
- Global new-episode monitoring using Podcast Index recent/data with a moving since cursor.
- Automatic discovery of Claude / Claude Code podcast feeds.
- Persistent podcast_sources.json registry for useful RSS feeds.
- Direct RSS/Atom monitoring of discovered feeds.
- Exact Claude referral extraction from podcast titles, descriptions and show notes.
- Shared validator/dedupe/Telegram pipeline for podcast referrals.
- Automatic retry/backoff on Podcast Index/network errors.
- podcast-test and watch-podcasts CLI commands.
- Podcast/RSS status in both CLI status and Telegram status.

Runtime-generated podcast_sources.json is ignored by git.
