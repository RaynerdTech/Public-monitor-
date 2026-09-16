Referral Monitor v10.6.1 - credit alert accuracy fix

Fixes:
- Ignore a single impossible Scrape Creators balance collapse (for example 7,063 -> 42) until a second request confirms it.
- Prevent small out-of-order balance increases from resetting already-sent low-credit thresholds.
- Apify usage forecast now waits for a 30-minute observation window instead of extrapolating one 5-minute billing jump.
- Apify observed burn is capped conservatively against the configured measured-cost estimate.

No new Render environment variables are required.

Recommended existing values:
CREDIT_MONITOR_INTERVAL_SECONDS=300
CREDIT_ALERT_THRESHOLDS_HOURS=72,24,6,3,1
APIFY_RENDER_AUTO_DEPLOY=false

Verification performed here:
- Python compilation passed.
- 9 credit/Scrape Creators tests passed.
- Full project suite could not be run in this environment because aiosqlite is unavailable; run pytest locally before pushing.
