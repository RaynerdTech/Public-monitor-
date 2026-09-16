from app.services.telegram_admin import parse_apify_token_command


def test_parse_apify_token_command():
    assert parse_apify_token_command("/set_apify_token apify_api_abc123") == "apify_api_abc123"
    assert parse_apify_token_command("/set_apify_token@ReferralBot apify_api_xyz") == "apify_api_xyz"
    assert parse_apify_token_command("/set_apify_token") == ""
    assert parse_apify_token_command("hello") is None
