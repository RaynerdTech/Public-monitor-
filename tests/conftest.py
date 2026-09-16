import pytest


@pytest.fixture(autouse=True)
def isolate_credit_monitor_tests(monkeypatch):
    """Keep unit tests from using real Telegram credentials or leaked state.

    The application loads values from the developer's local .env. Without this
    isolation, a mocked Scrape Creators balance in a unit test can accidentally
    be treated as a real low-balance event and send a Telegram message.
    """
    import app.services.credit_monitor as cm

    def reset_state() -> None:
        cm._alert_states.clear()
        cm._apify_usage_states.clear()
        cm._last_scrape_creators_balance = None
        cm._last_scrape_creators_hours = None
        cm._scrape_creators_suspect_balance = None
        cm._last_apify_remaining_usd = None
        cm._last_apify_hours = None

    reset_state()

    # Tests must never send real credit alerts, even when a developer's local
    # .env contains production Telegram credentials.
    monkeypatch.setattr(cm, "TELEGRAM_BOT_TOKEN", "")

    yield

    reset_state()
