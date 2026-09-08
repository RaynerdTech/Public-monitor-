Referral Monitor v2 update (changed/new files only)

What this adds:
- Central processing pipeline
- Deduplication by referral CODE, even when query parameters differ
- Occurrence/source tracking
- Validator retries and clearer error handling
- Telegram setup/test commands and richer alerts
- Local live file watcher for end-to-end testing
- Generic public web-page watcher
- Recent-results CLI
- Additional tests

After copying this update into the project:
1. Activate .venv
2. Run: pytest
3. Run: python -m app.main --help

Quick local live test:
Terminal 1:
  python -m app.main watch-file watch-input.txt --interval 2

Terminal 2:
  Add a referral URL to watch-input.txt, for example:
  https://claude.ai/referral/local-test-001

Simulation without relying on Claude's live validation endpoint:
  python -m app.main simulate "https://claude.ai/referral/demo-001"

Telegram setup:
1. Put TELEGRAM_BOT_TOKEN in .env
2. Message your bot once
3. Run: python -m app.main telegram-chats
4. Put the shown TELEGRAM_CHAT_ID in .env
5. Restart the CLI and run: python -m app.main telegram-test

Note: watch-url is for ordinary public pages. Platform-specific real-time
watchers (Threads/X/Reddit/Telegram) will plug into the same pipeline next.
