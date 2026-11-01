"""Names as typed, and their slugs for URLs and IDs."""

import re
import unicodedata

SLUG_MAX = 63


def slug(text: str) -> str:
    """'Seaside Windows & Doors' -> 'seaside-windows-doors' (the web app's slugify)."""
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:SLUG_MAX].rstrip("-")
