import os
from dotenv import load_dotenv


load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

def _telegram_chat_ids() -> list[str]:
    # TELEGRAM_CHAT_IDS is the canonical setting. Keep TELEGRAM_CHAT_ID as a
    # backwards-compatible fallback, including comma-separated values.
    raw = os.getenv("TELEGRAM_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID", "")
    return [chat_id.strip() for chat_id in raw.split(",") if chat_id.strip()]


TELEGRAM_CHAT_IDS = _telegram_chat_ids()
TELEGRAM_CHAT_ID = TELEGRAM_CHAT_IDS[0] if TELEGRAM_CHAT_IDS else ""
DATABASE_PATH = os.getenv("DATABASE_PATH", "referrals.db").strip() or "referrals.db"
VALIDATION_TIMEOUT_SECONDS = float(os.getenv("VALIDATION_TIMEOUT_SECONDS", "10"))
VALIDATION_RETRIES = max(1, int(os.getenv("VALIDATION_RETRIES", "2")))
VALIDATION_QUEUE_POLL_SECONDS = max(
    1, int(os.getenv("VALIDATION_QUEUE_POLL_SECONDS", "2"))
)
VALIDATION_QUEUE_WORKERS = min(
    8, max(1, int(os.getenv("VALIDATION_QUEUE_WORKERS", "3")))
)
VALIDATION_RETRY_BASE_SECONDS = max(
    2, int(os.getenv("VALIDATION_RETRY_BASE_SECONDS", "5"))
)
VALIDATION_RETRY_MAX_SECONDS = max(
    VALIDATION_RETRY_BASE_SECONDS,
    int(os.getenv("VALIDATION_RETRY_MAX_SECONDS", "120")),
)
TELEGRAM_RETRY_BASE_SECONDS = max(
    2, int(os.getenv("TELEGRAM_RETRY_BASE_SECONDS", "5"))
)
TELEGRAM_RETRY_MAX_SECONDS = max(
    TELEGRAM_RETRY_BASE_SECONDS,
    int(os.getenv("TELEGRAM_RETRY_MAX_SECONDS", "300")),
)
VALIDATOR_BROWSER_FALLBACK_ENABLED = _env_bool(
    "VALIDATOR_BROWSER_FALLBACK_ENABLED", True
)
VALIDATOR_BROWSER_HEADLESS = _env_bool("VALIDATOR_BROWSER_HEADLESS", False)
VALIDATOR_BROWSER_PROFILE_DIR = (
    os.getenv("VALIDATOR_BROWSER_PROFILE_DIR", ".referral-browser-profile").strip()
    or ".referral-browser-profile"
)
VALIDATOR_BROWSER_CHANNEL = os.getenv("VALIDATOR_BROWSER_CHANNEL", "chrome").strip()
VALIDATOR_BROWSER_CHALLENGE_WAIT_SECONDS = max(
    5, int(os.getenv("VALIDATOR_BROWSER_CHALLENGE_WAIT_SECONDS", "120"))
)
VALIDATOR_BROWSER_NAVIGATION_TIMEOUT_SECONDS = max(
    5, int(os.getenv("VALIDATOR_BROWSER_NAVIGATION_TIMEOUT_SECONDS", "30"))
)
URL_WATCH_INTERVAL_SECONDS = max(5, int(os.getenv("URL_WATCH_INTERVAL_SECONDS", "30")))

THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
THREADS_APP_ID = os.getenv("THREADS_APP_ID", "").strip()
THREADS_APP_SECRET = os.getenv("THREADS_APP_SECRET", "").strip()
THREADS_TOKEN_FILE = os.getenv("THREADS_TOKEN_FILE", ".threads-token.json").strip() or ".threads-token.json"
THREADS_OAUTH_HOST = os.getenv("THREADS_OAUTH_HOST", "127.0.0.1").strip() or "127.0.0.1"
THREADS_OAUTH_PORT = max(1, min(65535, int(os.getenv("THREADS_OAUTH_PORT", "8765"))))
THREADS_REDIRECT_URI = os.getenv("THREADS_REDIRECT_URI", "").strip()
THREADS_OAUTH_START_KEY = os.getenv("THREADS_OAUTH_START_KEY", "").strip()
THREADS_OAUTH_SCOPES = [
    scope.strip()
    for scope in os.getenv(
        "THREADS_OAUTH_SCOPES",
        "threads_basic,threads_keyword_search",
    ).split(",")
    if scope.strip()
]
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


EXA_API_KEY = os.getenv("EXA_API_KEY", "").strip()
EXA_WATCH_INTERVAL_SECONDS = max(
    60, int(os.getenv("EXA_WATCH_INTERVAL_SECONDS", "1200"))
)
EXA_SEARCH_LIMIT = min(100, max(1, int(os.getenv("EXA_SEARCH_LIMIT", "10"))))
EXA_LOOKBACK_MINUTES = max(5, int(os.getenv("EXA_LOOKBACK_MINUTES", "60")))
EXA_SEARCH_TYPE = os.getenv("EXA_SEARCH_TYPE", "fast").strip() or "fast"
EXA_PAGE_FETCH_TIMEOUT_SECONDS = max(
    3, int(os.getenv("EXA_PAGE_FETCH_TIMEOUT_SECONDS", "12"))
)
# Keep one broad query by default to control cost. Separate extra queries with ||.
EXA_QUERIES = [
    query.strip()
    for query in os.getenv(
        "EXA_QUERIES",
        'recent public webpages containing a Claude referral URL starting with https://claude.ai/referral/ or a Claude Code guest pass',
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

PODCAST_INDEX_API_KEY = os.getenv("PODCAST_INDEX_API_KEY", "").strip()
PODCAST_INDEX_API_SECRET = os.getenv("PODCAST_INDEX_API_SECRET", "").strip()
PODCAST_INDEX_USER_AGENT = (
    os.getenv("PODCAST_INDEX_USER_AGENT", "ReferralMonitor/0.9").strip()
    or "ReferralMonitor/0.9"
)
PODCAST_INDEX_WATCH_INTERVAL_SECONDS = max(
    30, int(os.getenv("PODCAST_INDEX_WATCH_INTERVAL_SECONDS", "120"))
)
PODCAST_INDEX_RECENT_MAX = max(
    1, int(os.getenv("PODCAST_INDEX_RECENT_MAX", "1000"))
)
PODCAST_LOOKBACK_MINUTES = max(
    5, int(os.getenv("PODCAST_LOOKBACK_MINUTES", "30"))
)
PODCAST_DISCOVERY_INTERVAL_SECONDS = max(
    300, int(os.getenv("PODCAST_DISCOVERY_INTERVAL_SECONDS", "21600"))
)
PODCAST_DISCOVERY_QUERIES = [
    query.strip()
    for query in os.getenv(
        "PODCAST_DISCOVERY_QUERIES",
        "Claude||Claude Code",
    ).split("||")
    if query.strip()
]
PODCAST_RSS_INTERVAL_SECONDS = max(
    30, int(os.getenv("PODCAST_RSS_INTERVAL_SECONDS", "120"))
)
PODCAST_RSS_MAX_FEEDS = max(
    1, int(os.getenv("PODCAST_RSS_MAX_FEEDS", "100"))
)
PODCAST_SOURCE_REGISTRY = (
    os.getenv("PODCAST_SOURCE_REGISTRY", "podcast_sources.json").strip()
    or "podcast_sources.json"
)
