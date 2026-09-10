Referral Monitor v8.3 - Telegram multi-destination access

- Adds TELEGRAM_CHAT_IDS for comma-separated Telegram destinations.
- Keeps TELEGRAM_CHAT_ID backwards compatible, even if it contains a comma-separated list.
- Valid referral alerts are sent independently to every configured chat.
- Adds `python -m app.main telegram-status` to send a concise system configuration snapshot to all chats.
- `telegram-test` now tests all configured destinations.

Recommended .env:
TELEGRAM_CHAT_IDS=1744069860,-5417859725
