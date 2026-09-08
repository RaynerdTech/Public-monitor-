Referral Monitor v5 update

Changed/new files only.

What changed:
- X production watcher now uses the official Filtered Stream persistent connection.
- Strict default X rule: url:"claude.ai/referral".
- Correctly extracts expanded URLs from X entities (Post text normally contains t.co only).
- x-stream-setup command manages only rules tagged refmon: and leaves unrelated rules alone.
- x-stream-rules command shows active X stream rules.
- x-test remains available for recent-search diagnostics/backfill.
- watch-x now runs live stream mode instead of polling every 20 seconds.

After applying:
  pytest
  python -m app.main status
  python -m app.main x-stream-setup
  python -m app.main x-stream-rules
  python -m app.main watch-x

Keep Auto Recharge disabled in the X Developer Console while testing and set a billing-cycle spend cap there if desired.
