import importlib

from app.core.models import ReferralCandidate
from app.services.telegram import format_referral_keyboard, parse_referral_feedback


def test_multiple_telegram_chat_ids(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "1744069860, -5417859725")
    import app.config as config
    importlib.reload(config)
    assert config.TELEGRAM_CHAT_IDS == ["1744069860", "-5417859725"]
    assert config.TELEGRAM_CHAT_ID == "1744069860"


def test_legacy_telegram_chat_id_accepts_comma_list(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1, -2")
    import app.config as config
    importlib.reload(config)
    assert config.TELEGRAM_CHAT_IDS == ["1", "-2"]


def test_referral_alert_keyboard_has_open_and_feedback_actions():
    candidate = ReferralCandidate(
        referral_url="https://claude.ai/referral/test-code",
        referral_code="test-code",
        source="test",
    )
    keyboard = format_referral_keyboard(candidate)["inline_keyboard"]
    assert keyboard[0][0]["url"] == candidate.referral_url
    assert parse_referral_feedback(keyboard[1][0]["callback_data"]) == (
        "worked",
        "test-code",
    )
