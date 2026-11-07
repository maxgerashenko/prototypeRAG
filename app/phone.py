"""One E.164 check for every place that takes a phone number (booking tools, the transfer
number, the business-number setting)."""

import re

# '+' then 7-15 digits, first one non-zero (E.164 allows at most 15 digits in total).
E164 = re.compile(r"\+[1-9]\d{6,14}")


def is_e164(value: str) -> bool:
    return E164.fullmatch(value) is not None


def normalize_phone(raw: str | None) -> str | None:
    """'+1 (555) 010-0100' -> '+15550100100'; None if it isn't a full international number."""
    if not raw:
        return None
    digits = re.sub(r"[\s\-().]", "", raw.strip())
    return digits if is_e164(digits) else None
