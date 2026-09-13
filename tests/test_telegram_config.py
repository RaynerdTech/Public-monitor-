import importlib
from datetime import datetime, timedelta, timezone

from app.core.models import ReferralCandidate
from app.services.telegram import (
    format_feedback_result,
    format_referral_alert,
    format_referral_keyboard,
    parse_referral_feedback,
)
from app.services.validator import ValidationResult


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


def test_referral_alert_uses_plain_friendly_language():
    detected = datetime(2026, 9, 13, 20, 0, tzinfo=timezone.utc)
    candidate = ReferralCandidate(
        referral_url="https://claude.ai/referral/test-code",
        referral_code="test-code",
        source="x-reply:@tester",
        source_url="https://x.com/tester/status/123",
        post_created_at=detected - timedelta(seconds=20),
        detected_at=detected,
    )

    alert = format_referral_alert(candidate, ValidationResult(status="pending"))

    assert "NEW CLAUDE REFERRAL FOUND" in alert
    assert "Found on: X reply by @tester" in alert
    assert "Still checking. Do not wait" in alert
    assert "Found after: under 1 minute" in alert
    assert "validation_status" not in alert


def test_feedback_result_is_easy_to_understand():
    updated = format_feedback_result("🚨 NEW CLAUDE REFERRAL FOUND", "unusable")
    assert "User result: ❌ Already used or invalid" in updated
