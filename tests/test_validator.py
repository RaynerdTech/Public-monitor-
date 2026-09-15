import asyncio

import pytest

from app.services import validator
from app.services.validator import ValidationResult, classify_referral_payload


def test_validation_result_shape():
    result = ValidationResult(
        status="valid",
        campaign="claude_code_guest_pass_a47c",
        is_valid=True,
    )
    assert result.status == "valid"
    assert result.is_valid is True


def test_classify_valid_guest_pass():
    result = classify_referral_payload(
        {"campaign": "claude_code_guest_pass_a47c", "is_valid": True}
    )
    assert result.status == "valid"


def test_classify_base_guest_pass_campaign():
    result = classify_referral_payload(
        {"campaign": "claude_code_guest_pass", "is_valid": True}
    )
    assert result.status == "valid"


def test_classify_contest():
    result = classify_referral_payload(
        {"campaign": "claude_invite_contest", "is_valid": True}
    )
    assert result.status == "contest"


def test_classify_inactive():
    result = classify_referral_payload(
        {"campaign": "claude_code_guest_pass_a47c", "is_valid": False}
    )
    assert result.status == "inactive"


def test_classify_not_found():
    result = classify_referral_payload(None)
    assert result.status == "not_found"


def test_other_true_campaign_is_classified_as_contest():
    result = classify_referral_payload(
        {"campaign": "some_other_campaign", "is_valid": True}
    )
    assert result.status == "contest"
    assert "some_other_campaign" in (result.message or "")


def test_cloudflare_response_is_blocked():
    result = validator._classify_http_response(
        403,
        "<html><title>Just a moment...</title><script src='https://challenges.cloudflare.com/x'></script></html>",
        "text/html",
        method="direct",
    )
    assert result.status == "blocked"


def test_plain_403_response_is_blocked_for_fallback():
    result = validator._classify_http_response(
        403,
        "Forbidden",
        "text/plain",
        method="direct",
    )
    assert result.status == "blocked"


def test_http_404_is_not_found():
    result = validator._classify_http_response(
        404,
        "Not found",
        "text/plain",
        method="direct",
    )
    assert result.status == "not_found"


def test_retry_after_supports_seconds_and_http_date():
    assert validator._retry_after_seconds("2") == 2
    assert validator._retry_after_seconds("invalid") is None
    assert validator._retry_after_seconds("Wed, 21 Oct 2099 07:28:00 GMT") > 0


@pytest.mark.anyio
async def test_direct_request_gate_serializes_requests(monkeypatch):
    monkeypatch.setattr(validator, "VALIDATION_MIN_GAP_SECONDS", 0)
    gate = validator._DirectRequestGate()

    class Client:
        active = 0
        maximum_active = 0

        async def get(self, _url):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            return object()

    client = Client()
    await asyncio.gather(gate.get(client, "one"), gate.get(client, "two"))
    assert client.maximum_active == 1


@pytest.mark.anyio
async def test_browser_fallback_used_after_cloudflare(monkeypatch):
    async def fake_direct(_code):
        return ValidationResult(status="blocked", message="Cloudflare browser challenge")

    class FakeBrowserResult:
        status_code = 200
        text = '{"campaign":"claude_code_guest_pass_a47c","is_valid":true}'
        content_type = "application/json"
        message = None

    async def fake_browser(_code):
        return FakeBrowserResult()

    monkeypatch.setattr(validator, "_validate_direct", fake_direct)
    monkeypatch.setattr(validator, "VALIDATOR_BROWSER_FALLBACK_ENABLED", True)

    import app.services.browser_validator as browser_validator

    monkeypatch.setattr(browser_validator, "fetch_referral_api_in_browser", fake_browser)
    result = await validator.validate_referral("abc123")

    assert result.status == "valid"
    assert result.method == "browser"
