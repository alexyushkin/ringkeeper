"""A service saying the account's plan doesn't include this: not a fault, and not worth a retry.

A plan or entitlement refusal looks like any other failure from the outside, so each error is
read here once, for every kind of connection (services.calls). What it finds isn't a broken key or
a bad call: nothing an agent does differently will change it, so it mustn't look like a failed
attempt. A rate limit that mentions upgrading isn't one of these: it passes with time.
"""

import re

# Says it outright.
OUTRIGHT = re.compile(
    r"(?ix)"
    r"\b(your|current|this|the)\s+(\w+\s+){0,2}(plan|subscription|tier|license|licence)\b"
    r"[^.\n]{0,60}\b(doesn'?t|does\s+not|don'?t|do\s+not|isn'?t|is\s+not|not)\b"
    r"[^.\n]{0,40}\b(include|cover|support|allow|permit|have\s+access|grant|eligible|entitled)"
    r"|\bnot\s+(included|available|supported|enabled|allowed)\s+(in|on|for|with)\s+"
    r"(your|the\s+current|this|the\s+free)\s+(\w+\s+)?(plan|subscription|tier|license|licence)"
    r"|\b(not_entitled|plan_required|feature_not_available_on_plan|subscription_required"
    r"|upgrade_required|plan_upgrade_required)\b"
)
# Might be about a plan: only with a refusal beside it.
HINTS = re.compile(
    r"(?i)\b(upgrade\s+(your|to|the)|requires?\s+(an?\s+)?(paid|higher|pro|premium|business"
    r"|enterprise|upgraded)|paid\s+plan|entitlement|not\s+entitled|subscription\s+required)"
)
REFUSED = re.compile(r"(?i)\b(40[23]|forbidden|access_denied|access\s+denied|payment\s+required)\b")
PASSES_WITH_TIME = re.compile(r"(?i)\b(429|rate[\s_-]?limit|too\s+many\s+requests)\b")


def found(text: str) -> bool:
    """Whether this error is a plan or entitlement refusal."""
    if PASSES_WITH_TIME.search(text):
        return False
    return bool(OUTRIGHT.search(text) or (HINTS.search(text) and REFUSED.search(text)))


def message(service: str, note: str = "") -> str:
    """What the user reads, on the connection and the task."""
    return f"Your {service} plan doesn't include API access to this. {note}".strip()
