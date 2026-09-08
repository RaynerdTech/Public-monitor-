import os
from dotenv import load_dotenv


load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", "referrals.db").strip() or "referrals.db"
VALIDATION_TIMEOUT_SECONDS = float(os.getenv("VALIDATION_TIMEOUT_SECONDS", "10"))
VALIDATION_RETRIES = max(1, int(os.getenv("VALIDATION_RETRIES", "2")))
URL_WATCH_INTERVAL_SECONDS = max(5, int(os.getenv("URL_WATCH_INTERVAL_SECONDS", "30")))

THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
THREADS_WATCH_INTERVAL_SECONDS = max(
    10, int(os.getenv("THREADS_WATCH_INTERVAL_SECONDS", "30"))
)
THREADS_SEARCH_LIMIT = min(100, max(1, int(os.getenv("THREADS_SEARCH_LIMIT", "50"))))
THREADS_QUERIES = [
    query.strip()
    for query in os.getenv(
        "THREADS_QUERIES",
        "claude.ai/referral,claude referral",
    ).split(",")
    if query.strip()
]

X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "").strip()
X_WATCH_INTERVAL_SECONDS = max(10, int(os.getenv("X_WATCH_INTERVAL_SECONDS", "20")))
X_SEARCH_LIMIT = min(100, max(10, int(os.getenv("X_SEARCH_LIMIT", "100"))))
# Keep recent search very specific. X's url: operator matches expanded_url too.
X_QUERIES = [
    query.strip()
    for query in os.getenv(
        "X_QUERIES",
        'url:"claude.ai/referral"',
    ).split("||")
    if query.strip()
]
# Production/live monitoring uses Filtered Stream rather than polling recent search.
X_STREAM_RULES = [
    rule.strip()
    for rule in os.getenv(
        "X_STREAM_RULES",
        'url:"claude.ai/referral"',
    ).split("||")
    if rule.strip()
]
X_STREAM_RECONNECT_SECONDS = max(
    1, int(os.getenv("X_STREAM_RECONNECT_SECONDS", "5"))
)
