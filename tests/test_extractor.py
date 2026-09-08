from app.core.extractor import extract_referral_code, extract_referral_links


def test_extract_referral_links():
    text = """
    Here is one:
    https://claude.ai/referral/abc123

    Another:
    https://claude.ai/referral/test-code_456?s=android
    """

    links = extract_referral_links(text)

    assert links == [
        "https://claude.ai/referral/abc123",
        "https://claude.ai/referral/test-code_456?s=android",
    ]


def test_extract_referral_code():
    code = extract_referral_code("https://claude.ai/referral/test-code_456?s=android")
    assert code == "test-code_456"
