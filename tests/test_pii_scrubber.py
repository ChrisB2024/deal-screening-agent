from app.services.pii_scrubber import scrub_pii


def test_scrub_pii_redacts_common_pii_patterns():
    text = (
        "Contact: John Smith\n"
        "Email: john@example.com\n"
        "Phone: 212-555-1212\n"
        "SSN: 123-45-6789\n"
        "Card: 4111 1111 1111 1111\n"
    )

    scrubbed = scrub_pii(text)

    assert "[NAME_REDACTED]" in scrubbed
    assert "[EMAIL_REDACTED]" in scrubbed
    assert "[PHONE_REDACTED]" in scrubbed
    assert "[SSN_REDACTED]" in scrubbed
    assert "[CC_REDACTED]" in scrubbed


def test_scrub_pii_preserves_deal_relevant_business_text():
    text = (
        "The company generated 5000000 of revenue and 1200000 of EBITDA.\n"
        "Sector: healthcare. Geography: US Southeast."
    )

    scrubbed = scrub_pii(text)

    assert "5000000" in scrubbed
    assert "1200000" in scrubbed
    assert "healthcare" in scrubbed
    assert "US Southeast" in scrubbed
