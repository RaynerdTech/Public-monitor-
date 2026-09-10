import importlib


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
