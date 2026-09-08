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

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "").strip()
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
REDDIT_USER_AGENT = os.getenv(
    "REDDIT_USER_AGENT",
    "windows:referral-monitor:v0.6",
).strip()
REDDIT_WATCH_INTERVAL_SECONDS = max(
    10, int(os.getenv("REDDIT_WATCH_INTERVAL_SECONDS", "20"))
)
REDDIT_SEARCH_LIMIT = min(100, max(1, int(os.getenv("REDDIT_SEARCH_LIMIT", "100"))))
REDDIT_QUERIES = [
    query.strip()
    for query in os.getenv(
        "REDDIT_QUERIES",
        'claude.ai/referral||"Claude referral"||"Claude guest pass"',
    ).split("||")
    if query.strip()
]

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
YOUTUBE_WATCH_INTERVAL_SECONDS = max(
    60, int(os.getenv("YOUTUBE_WATCH_INTERVAL_SECONDS", "1200"))
)
YOUTUBE_SEARCH_LIMIT = min(50, max(1, int(os.getenv("YOUTUBE_SEARCH_LIMIT", "50"))))
YOUTUBE_LOOKBACK_MINUTES = max(5, int(os.getenv("YOUTUBE_LOOKBACK_MINUTES", "30")))
# Keep this as one OR-style query by default. YouTube currently gives search.list
# its own default bucket of 100 calls/day, so 1200s uses about 72 calls/day and leaves room for tests.
# Separate extra queries with || only if needed.
YOUTUBE_QUERIES = [
    query.strip()
    for query in os.getenv(
        "YOUTUBE_QUERIES",
        "claude referral|claude guest pass|claude.ai/referral",
    ).split("||")
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
