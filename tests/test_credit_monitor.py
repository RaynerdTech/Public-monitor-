from app.services.credit_monitor import (
    _AlertState,
    _threshold_to_send,
    estimate_hours_remaining,
)


def test_estimate_hours_remaining():
    assert estimate_hours_remaining(120, 24) == 5
    assert estimate_hours_remaining(10, 0) is None


def test_threshold_selects_only_closest_warning():
    state = _AlertState()
    # At 20 hours left, the 24h warning is the correct single warning.
    assert _threshold_to_send(state, 20) == 24
    state.alerted_thresholds.update({72, 24})
    # Once 24h has fired, do not backfill the 72h warning.
    assert _threshold_to_send(state, 20) is None

from app.services.credit_monitor import _scrape_balance_is_suspicious


def test_scrape_balance_rejects_one_off_catastrophic_drop():
    assert _scrape_balance_is_suspicious(7063, 42) is True
    assert _scrape_balance_is_suspicious(7063, 7062) is False
    assert _scrape_balance_is_suspicious(100, 24) is True
    assert _scrape_balance_is_suspicious(100, 25) is False


def test_scrape_creators_first_observation_is_baseline(monkeypatch):
    import asyncio
    import app.services.credit_monitor as cm

    cm._last_scrape_creators_balance = None
    cm._last_scrape_creators_hours = None
    cm._scrape_creators_suspect_balance = None
    cm._alert_states.clear()

    sent = []

    async def fake_send(provider, text):
        sent.append((provider, text))

    monkeypatch.setattr(cm, "_send_alert", fake_send)
    asyncio.run(cm.observe_scrape_creators_balance(42, credits_charged=1))

    assert cm._last_scrape_creators_balance == 42
    assert sent == []


def test_scrape_creators_second_low_observation_can_alert(monkeypatch):
    import asyncio
    import app.services.credit_monitor as cm

    cm._last_scrape_creators_balance = None
    cm._last_scrape_creators_hours = None
    cm._scrape_creators_suspect_balance = None
    cm._alert_states.clear()

    sent = []

    async def fake_send(provider, text):
        sent.append((provider, text))

    monkeypatch.setattr(cm, "_send_alert", fake_send)
    asyncio.run(cm.observe_scrape_creators_balance(42, credits_charged=1))
    asyncio.run(cm.observe_scrape_creators_balance(41, credits_charged=1))

    assert cm._last_scrape_creators_balance == 41
    assert len(sent) == 1


def test_low_credit_alert_is_short_and_user_friendly(monkeypatch):
    import asyncio
    import app.services.credit_monitor as cm

    sent = []

    async def fake_send(provider, text):
        sent.append((provider, text))

    monkeypatch.setattr(cm, "_send_alert", fake_send)
    monkeypatch.setattr(cm, "CREDIT_ALERT_THRESHOLDS_HOURS", [72, 24, 6, 3, 1])
    cm._alert_states.clear()

    asyncio.run(
        cm._evaluate_balance(
            state_key="friendly-test",
            provider="Apify",
            remaining=10.0,
            burn_per_hour=1.0,
            unit_label="USD",
            remaining_display="$10.00",
        )
    )

    assert len(sent) == 1
    message = sent[0][1]
    assert "⚠️ Apify credit low" in message
    assert "Balance: $10.00" in message
    assert "Time left:" in message
    assert "estimated use" not in message.lower()
    assert "\n\n" in message
