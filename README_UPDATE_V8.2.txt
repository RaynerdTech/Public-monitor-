Referral Monitor v8.2 - strict fresh web discovery

Changes:
- Exa now receives startPublishedDate on every search so stale historical pages are filtered server-side.
- Broad web discovery now requires a real recent publishedDate; random undated results are skipped.
- 404/410 live pages remain rejected.
- Future/malformed dates are rejected.
- Default Exa polling interval changed to 1200 seconds (20 minutes) to control cost.
- Default Exa query tightened around Claude referral URLs / Claude Code guest passes.
- Known useful domains should be promoted to direct monitoring instead of allowing undated pages into broad discovery.

IMPORTANT: If your local .env already contains EXA_WATCH_INTERVAL_SECONDS=300, change it manually to 1200 because update ZIPs do not overwrite .env.
