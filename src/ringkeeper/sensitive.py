"""Raw card and bank account numbers: Ringkeeper never handles them.

A tool call that carries one is refused before anything else happens (`find`), and
`mask` takes them out of everything kept: logs, audit rows, a task's events and
conversations (ringkeeper.scrub applies it wherever a secret is scrubbed).

What counts, so that an order number or a timestamp doesn't:

- a card: a known card network's opening digits and length, passing the Luhn check,
  written as one number or in the usual groups of digits;
- an IBAN: its country letters and check digits, passing the mod-97 check;
- a number in a field or after a label that says what it is ("account number",
  "routing number", "cvv"). A last four is too short to count.
"""

import re
from typing import cast

MASK = "[card or bank number removed]"
CARD_WORD = "a card number"
BANK_WORD = "a bank account number"

_CARD = re.compile(
    r"(?<![\w.+-])(?:\d{13,19}|\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,7}|\d{4}[ -]\d{6}[ -]\d{4,5})"
    r"(?![\w-])"
)
_IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}(?![A-Za-z0-9])")
IBAN_COUNTRIES = frozenset(
    [
        "AD",
        "AE",
        "AL",
        "AT",
        "AZ",
        "BA",
        "BE",
        "BG",
        "BH",
        "BI",
        "BR",
        "BY",
        "CH",
        "CR",
        "CY",
        "CZ",
        "DE",
        "DJ",
        "DK",
        "DO",
        "EE",
        "EG",
        "ES",
        "FI",
        "FK",
        "FO",
        "FR",
        "GB",
        "GE",
        "GI",
        "GL",
        "GR",
        "GT",
        "HN",
        "HR",
        "HU",
        "IE",
        "IL",
        "IQ",
        "IS",
        "IT",
        "JO",
        "KW",
        "KZ",
        "LB",
        "LC",
        "LI",
        "LT",
        "LU",
        "LV",
        "LY",
        "MC",
        "MD",
        "ME",
        "MK",
        "MN",
        "MR",
        "MT",
        "MU",
        "NI",
        "NL",
        "NO",
        "OM",
        "PK",
        "PL",
        "PS",
        "PT",
        "QA",
        "RO",
        "RS",
        "RU",
        "SA",
        "SC",
        "SD",
        "SE",
        "SI",
        "SK",
        "SM",
        "SO",
        "ST",
        "SV",
        "TL",
        "TN",
        "TR",
        "UA",
        "VA",
        "VG",
        "XK",
        "YE",
    ]
)
# "account number: 12345678", "routing_number=021000021", "\"iban\": \"...\""
_LABELLED = re.compile(
    r"(?i)(?P<label>\b(?:bank[ _-]?account(?:[ _-]?(?:number|no|num))?"
    r"|account[ _-]?(?:number|no\b|num\b)"
    r"|acct[ _-]?(?:number|no\b|num\b)|routing[ _-]?(?:number|no\b|num\b)|aba[ _-]?(?:number|no\b)"
    r"|sort[ _-]?code|cvv2?|cvc2?|card[ _-]?(?:number|no\b|num\b)|credit[ _-]?card)"
    r"[\"']?\s*(?:is\s+|[:=#]\s*)?[\"']?)(?P<number>\d[\d -]{2,22}\d)"
)
# A field that holds one of these, by its name.
_CARD_KEY = re.compile(
    r"(?i)^(card|cc|pan|cardnumber|card_?(number|no|num)|credit_?card(_?number)?|cc_?(number|num)"
    r"|cvv2?|cvc2?|card_?(cvv|cvc|code)|security_?code)$"
)
_BANK_KEY = re.compile(
    r"(?i)^(iban|bank_?account(_?(number|no|num))?|account_?(number|no|num)|acct_?(number|no|num)"
    r"|routing(_?(number|no|num))?|aba(_?(number|no))?|sort_?code|bsb)$"
)
_SHORT_KEY = re.compile(r"(?i)^(cvv2?|cvc2?|card_?(cvv|cvc|code)|security_?code)$")
MIN_DIGITS = 6  # a last four, or a short internal code, isn't one
CVV_DIGITS = (3, 4)
_CVV_LABEL = re.compile(r"(?i)cv[vc]")


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def _a_network(digits: str) -> bool:  # noqa: PLR0911 (one line per network)
    size, two, four = len(digits), int(digits[:2]), int(digits[:4])
    if digits[0] == "4":
        return size in (13, 16, 19)  # Visa
    if 51 <= two <= 55 or 2221 <= four <= 2720:
        return size == 16
    if two in (34, 37):
        return size == 15
    if four == 6011 or two == 65 or 644 <= int(digits[:3]) <= 649:
        return 16 <= size <= 19
    if 3528 <= four <= 3589:
        return 16 <= size <= 19
    if two in (36, 38) or 300 <= int(digits[:3]) <= 305:
        return 14 <= size <= 19
    if digits[:2] == "62":
        return 16 <= size <= 19
    return False


def _is_card(text: str) -> bool:
    digits = re.sub(r"[ -]", "", text)
    return digits.isdigit() and _a_network(digits) and _luhn(digits)


def _is_iban(text: str) -> bool:
    compact = text.replace(" ", "")
    if compact[:2] not in IBAN_COUNTRIES:
        return False
    moved = compact[4:] + compact[:4]
    number = "".join(str(int(char, 36)) for char in moved)
    return int(number) % 97 == 1


def _in_text(text: str) -> str | None:
    """What kind of number this text holds, or None."""
    if not any(char.isdigit() for char in text):
        return None
    if any(_is_card(found.group()) for found in _CARD.finditer(text)):
        return CARD_WORD
    if any(_is_iban(found.group()) for found in _IBAN.finditer(text)):
        return BANK_WORD
    for found in _LABELLED.finditer(text):
        if _counts(found.group("label"), found.group("number")):
            return CARD_WORD if re.search(r"(?i)card|cv[vc]", found.group("label")) else BANK_WORD
    return None


def _counts(label: str, number: str) -> bool:
    digits = sum(char.isdigit() for char in number)
    if _CVV_LABEL.search(label):
        return digits in CVV_DIGITS
    return digits >= MIN_DIGITS


def _in_field(key: str, value: object) -> str | None:
    """What a field named `key` holds, by its name, or None."""
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    digits = sum(char.isdigit() for char in str(value))
    if _SHORT_KEY.match(key):
        return CARD_WORD if digits in CVV_DIGITS else None
    if digits < MIN_DIGITS:
        return None
    if _CARD_KEY.match(key):
        return CARD_WORD
    return BANK_WORD if _BANK_KEY.match(key) else None


def field_holds(key: str, value: object) -> bool:
    return _in_field(key, value) is not None


def find(value: object) -> str | None:
    """The first card or bank number anywhere in a JSON-like value: what kind, or None."""
    if isinstance(value, str):
        return _in_text(value)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return _in_text(str(value))
    if isinstance(value, dict):
        for key, inner in cast(dict[object, object], value).items():
            found = _in_field(str(key), inner) or find(key) or find(inner)
            if found:
                return found
    elif isinstance(value, list | tuple):
        for inner in cast(list[object], value):
            if found := find(inner):
                return found
    return None


def mask(text: str) -> str:
    """The text with every card and bank number taken out."""
    if not any(char.isdigit() for char in text):
        return text
    text = _CARD.sub(lambda m: MASK if _is_card(m.group()) else m.group(), text)
    text = _IBAN.sub(lambda m: MASK if _is_iban(m.group()) else m.group(), text)

    def labelled(found: re.Match[str]) -> str:
        if _counts(found.group("label"), found.group("number")):
            return found.group("label") + MASK
        return found.group()

    return _LABELLED.sub(labelled, text)
