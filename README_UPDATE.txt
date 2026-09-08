Referral Monitor update package

Changed/new files only.

After extraction into your project root:
1. Activate .venv
2. Run: pip install -r requirements.txt
3. Run: pytest
4. Run: python -m app.main scan "https://claude.ai/referral/abc123"

Optional Telegram setup:
Copy .env.example values into your existing .env and fill them in.
