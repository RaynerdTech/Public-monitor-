import os

from app.services import browser_validator


def test_render_runtime_forces_headless_and_bundled_chromium(monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://example.onrender.com")
    assert browser_validator._is_hosted_runtime() is True
    assert browser_validator._browser_headless() is True


def test_local_runtime_keeps_configured_headless_setting(monkeypatch):
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr(browser_validator, "VALIDATOR_BROWSER_HEADLESS", False)
    assert browser_validator._is_hosted_runtime() is False
    assert browser_validator._browser_headless() is False


def test_hosted_relative_profile_moves_to_tmp(monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://example.onrender.com")
    monkeypatch.setattr(browser_validator, "VALIDATOR_BROWSER_PROFILE_DIR", ".referral-browser-profile")
    assert str(browser_validator._profile_dir()).startswith("/tmp/")
