"""Card and bank numbers: blocked as tool inputs, and taken out of everything kept."""

import logging

import pytest

from ringkeeper import scrub, sensitive

VISA = "4242424242424242"
AMEX = "378282246310005"
IBAN = "GB82 WEST 1234 5698 7654 32"


@pytest.mark.parametrize(
    "value",
    [
        VISA,
        "4242 4242 4242 4242",
        "4242-4242-4242-4242",
        AMEX,
        "3782 822463 10005",
        f"charge card {VISA} now",
        {"nested": [{"note": VISA}]},
        {"number": int(VISA)},
        {"card_number": "1234567890"},
        {"cvv": "123"},
        {"card_cvc": 1234},
    ],
)
def test_a_card_is_found(value: object) -> None:
    assert sensitive.find(value) == sensitive.CARD_WORD


@pytest.mark.parametrize(
    "value",
    [
        IBAN,
        "DE89370400440532013000",
        {"account_number": "000123456789"},
        {"routing_number": "021000021"},
        {"iban": "12345678"},
        "account number: 12345678",
        'routing_number="021000021"',
        '{"bank_account": "12345678"}',
    ],
)
def test_a_bank_number_is_found(value: object) -> None:
    assert sensitive.find(value) == sensitive.BANK_WORD


@pytest.mark.parametrize(
    "value",
    [
        "1730803200000",  # a timestamp in milliseconds
        "4242424242424241",  # fails Luhn
        "9999999999999995",  # passes Luhn, no card network
        "2026-11-05 12:00:00",
        "+15551234567",
        "order 100000000000000",
        "AC" + "1" * 32,
        "550e8400-e29b-41d4-a716-446655440000",
        "GB00 WEST 1234 5698 7654 32",  # bad check digits
        {"last4": "4242"},
        {"account_number": "1234"},  # too short to be one
        {"account": "1234567890"},  # a Google Ads customer ID, say
        {"cvv": "12"},
        "account 1234567890",
        True,
    ],
)
def test_ordinary_numbers_are_left_alone(value: object) -> None:
    assert sensitive.find(value) is None


def test_mask_takes_them_out_and_keeps_the_rest() -> None:
    text = f"paid with {VISA}, ref 1730803200000, iban {IBAN}, account number 12345678"
    masked = sensitive.mask(text)
    assert VISA not in masked
    assert "WEST" not in masked
    assert "12345678" not in masked
    assert "1730803200000" in masked
    assert "account number" in masked


def test_scrub_covers_text_fields_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    assert VISA not in scrub.scrub(f"card {VISA}")
    cleaned = scrub.scrub_obj({"account_number": "000123456789", "note": VISA, "id": "abc"})
    assert cleaned == {
        "account_number": sensitive.MASK,
        "note": sensitive.MASK,
        "id": "abc",
    }
    scrub.install_log_scrubbing()
    with caplog.at_level(logging.INFO):
        logging.getLogger("ringkeeper.test").info("got %s", VISA)
    assert VISA not in caplog.text
