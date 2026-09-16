Referral Monitor v10.6 - credit exhaustion alerts + safer defaults

What changed
------------
1. Automatic Telegram credit warnings for Scrape Creators and Apify.
   Default warning points: 72h, 24h, 6h, 3h, 1h before estimated exhaustion,
   plus an exhausted/limit-reached alert.

2. Scrape Creators does NOT spend extra credits just to monitor its balance.
   Threads/Reddit responses already contain credits_remaining, and v10.6 uses
   that balance to forecast time left from the configured polling intervals.

3. Apify balance monitoring uses the official /v2/users/me/limits endpoint.
   It checks every 5 minutes by default and does not launch a paid Actor.
   The ETA starts from the configured observed run-cost estimates and then
   learns from actual account usage changes while the service is running.

4. New Telegram admin command:
      /credits
   Alias:
      /credit_status
   This shows the latest Scrape Creators balance and Apify account-limit balance.

5. Apify token rotation remains immediate without needing a deploy.
   APIFY_RENDER_AUTO_DEPLOY now defaults to false. The running process switches
   to the new token immediately and Render stores it for the next restart.

6. Direct website lookback now defaults to 5 minutes instead of 180 minutes.

New optional Render variables
-----------------------------
CREDIT_MONITOR_INTERVAL_SECONDS=300
CREDIT_ALERT_THRESHOLDS_HOURS=72,24,6,3,1
APIFY_FACEBOOK_ESTIMATED_RUN_COST_USD=0.024
APIFY_INSTAGRAM_ESTIMATED_RUN_COST_USD=0.027

Recommended existing values
---------------------------
APIFY_RENDER_AUTO_DEPLOY=false
WEB_DIRECT_WATCH_INTERVAL_SECONDS=120
WEB_DIRECT_LOOKBACK_MINUTES=5

Notes
-----
- If you change Facebook/Instagram polling intervals in Render, the Apify
  forecast automatically uses those intervals.
- Apify Actor cost is variable, so the time-left warning is an estimate. The
  monitor improves that estimate from actual usage while it runs.
- Scrape Creators forecasting is more deterministic because the configured
  Threads/Reddit search requests currently cost 1 credit each.
