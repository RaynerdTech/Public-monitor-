Referral Monitor v9.7 - automatic validation retries and Render activity logs

Production behavior:
- Every discovered referral is stored and queued immediately.
- Watchers do not wait for validation, so one Cloudflare challenge cannot pause every source.
- pending, blocked, error, and unknown validations retry automatically with backoff.
- blocked is an attempt result, not a final link result.
- Only a confirmed status of valid creates Telegram deliveries.
- Telegram delivery is tracked and retried separately for every configured chat.
- Inactive, contest, not_found, blocked, error, unknown, and duplicate results never go to Telegram.

Render logs now include structured JSON events for:
- service, queue, and watcher startup
- configured and skipped watchers
- source polling and X stream connections
- discovered posts and referral codes
- duplicates
- validation attempts, results, and scheduled retries
- Telegram queueing, sends, failures, and retries
- Threads OAuth authorization, callbacks, and token refreshes
- watcher and queue failures

New optional environment variables:
VALIDATION_QUEUE_POLL_SECONDS=2
VALIDATION_QUEUE_WORKERS=3
VALIDATION_RETRY_BASE_SECONDS=5
VALIDATION_RETRY_MAX_SECONDS=120
TELEGRAM_RETRY_BASE_SECONDS=5
TELEGRAM_RETRY_MAX_SECONDS=300

The existing database is migrated automatically at startup. No manual SQL is needed.

After applying this update:
1. Run pytest.
2. Commit and push to main.
3. Let Render finish its Docker deployment.
4. Confirm the Render logs contain retry_queues_started and monitor_started.
5. Open /version and confirm it shows Referral Monitor v9.7.
6. Open /threads/authorize with the configured start key and complete Threads OAuth.
