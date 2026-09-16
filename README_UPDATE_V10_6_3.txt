Referral Monitor v10.6.3 — test isolation fix

Problem fixed:
- Running `pytest` with a real local .env could send a fake Scrape Creators
  low-credit Telegram alert because one unit test intentionally mocks a
  response with `credits_remaining: 42`.
- Credit-monitor module state could also leak between tests, making the mocked
  balance look like a confirmed low balance.

Fix:
- Added an autouse pytest fixture that resets credit-monitor in-memory state
  before and after every test.
- Credit-monitor Telegram sending is disabled during unit tests even if the
  local .env contains a real Telegram bot token.

Production runtime behavior is unchanged.
