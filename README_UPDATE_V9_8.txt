Referral Monitor v9.8 - immediate Telegram delivery

What changed:
- Every newly discovered Claude referral is queued for Telegram immediately.
- Telegram delivery runs before the optional server-side check.
- Cloudflare "blocked" no longer suppresses or delays a referral alert.
- Automatic validation stops after one attempt by default, so blocked links do not loop forever.
- The Telegram alert clearly says OPEN NOW and includes an Open referral button.
- Telegram includes Worked and Already used / invalid buttons.
- Button feedback is stored in the database and written to Render/VPS activity logs.
- Telegram delivery failures still retry independently so temporary Telegram outages do not lose alerts.

Important:
Claude does not provide a reliable public server-side way to prove that a referral is still
claimable. The only dependable final result is what the user sees after opening/claiming it.
That is why discovery is now sent immediately and the human result is recorded afterward.

Recommended production settings:
VALIDATION_MAX_ATTEMPTS=1
VALIDATOR_BROWSER_FALLBACK_ENABLED=false
TELEGRAM_FEEDBACK_POLL_SECONDS=5

Expected activity log events:
- telegram_delivery_queued
- telegram_delivery_started
- telegram_delivery_sent
- validation_completed
- validation_stopped (when the automatic check is inconclusive)
- telegram_feedback_recorded

After applying the update:
1. Run pytest.
2. Commit and push to main.
3. Wait for Render to deploy.
4. Open /version and confirm Referral Monitor v9.8.
5. Confirm logs contain telegram_feedback_started.
6. A newly discovered referral should arrive immediately even if validation says blocked.
