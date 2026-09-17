Referral Monitor v10.7.1

Hotfix for v10.7.0 startup crash.

Fixed:
- Removed YOUTUBE_DAILY_SEARCH_QUOTA from all YouTubeWatcher constructor calls.
- YouTubeWatcher accepts only api_key and queries as positional arguments; quota remains status/credit-tracking configuration only.
- watch-all, youtube-test, watch-youtube and the single-query YouTube test now instantiate YouTubeWatcher correctly.
- Bumped APP_VERSION to 10.7.1.

No watcher behavior, environment variable names, search intervals, credit logic, Telegram logic, or provider configuration were otherwise changed.
