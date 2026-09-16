Referral Monitor v10.6.2

Fixes false Scrape Creators low-credit alerts after Render deploy/restart.

Why it happened:
- credit alert state lives in memory
- a deploy resets that state
- the first Scrape Creators balance after startup could transiently report a wrong low value
- because there was no previous baseline, v10.6.1 could not identify it as an anomaly

Fix:
- first Scrape Creators balance after process startup is baseline-only
- no low-credit Telegram alert is sent from that first reading
- a genuinely low balance must be confirmed by the next source request before warning
- normal /credits status still receives the baseline immediately

No new environment variables are required.
