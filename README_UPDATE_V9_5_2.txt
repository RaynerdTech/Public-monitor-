Referral Monitor v9.5.2 - hosted validator diagnostic / secret log hardening

What changed
- Adds optional VALIDATOR_STARTUP_TEST_CODE for a one-shot validator test on startup.
- Designed for Render Free, where Shell/one-off jobs are unavailable.
- Logs status, validation method, campaign, and error/message without printing tokens.
- Stops printing THREADS_OAUTH_START_KEY inside the startup authorization URL.

How to test on Render
1. Set VALIDATOR_STARTUP_TEST_CODE=Vck1Go3v3w
2. Redeploy.
3. In logs, find "Hosted validator result".
4. Expected for this known inactive guest pass: status=inactive, method=browser.
5. Remove VALIDATOR_STARTUP_TEST_CODE and redeploy after the test.

Security
Rotate THREADS_OAUTH_START_KEY because an older deployment printed it to Render logs.
