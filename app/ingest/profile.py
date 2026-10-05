"""Structured business-profile extraction from the website (plan/01-crawler.md step 4).

DEC-11/R9: the website and the owner are the source of truth for stored facts. From
Places we only ever keep `place_id`, for a later live lookup — never store other
Places fields here.
"""

from app.llm import chat_json

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": ["string", "null"]},
        "address": {"type": ["string", "null"]},
        "phone": {"type": ["string", "null"]},
        "email": {"type": ["string", "null"]},
        "booking_policy": {"type": ["string", "null"]},
        "price_range": {"type": ["string", "null"]},
        "opening_hours": {"type": ["string", "null"]},
        "languages": {"type": ["array", "null"], "items": {"type": "string"}},
    },
    "required": [
        "name", "address", "phone", "email",
        "booking_policy", "price_range", "opening_hours", "languages",
    ],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "Extract business profile facts ONLY from the provided website text. "
    "Use null for anything not explicitly stated — never guess or infer."
)


def extract_profile_from_text(pages_markdown: list[str], business_name_hint: str | None = None) -> dict:
    """Ask the LLM to fill PROFILE_SCHEMA from the crawled pages' Markdown."""
    text = "\n\n".join(pages_markdown)[:12000]  # keep the start; most-important facts tend to be on top pages
    user_message = f"Business name hint: {business_name_hint}\n\n{text}" if business_name_hint else text

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    result = chat_json(messages, PROFILE_SCHEMA, "business_profile")
    return {k: _none_if_placeholder(k, v) for k, v in result.items()}


_PLACEHOLDERS = {"null", "none", "n/a", "unknown"}


def _none_if_placeholder(key: str, value):
    """Models sometimes fill an unstated field with a placeholder instead of JSON null:
    the string "null" (seen with qwen3.6-35b-a3b) or the field's own name ("business_name"
    for `name`, seen on the pilot -- the voice greeting then said "you've reached
    business_name"). Treat those like null."""
    if not isinstance(value, str):
        return value
    v = value.strip().lower()
    return None if v in _PLACEHOLDERS or v in (key, f"business_{key}") else value


_TITLE_SEPARATORS = (" | ", " — ", " – ", " - ", " · ", " :: ")


def name_from_title(title: str | None) -> str | None:
    """Fallback business name from a page <title>: the part before the first separator
    ("Bathhouse Williamsburg | Sauna, Steam & Cold Plunge Brooklyn" -> "Bathhouse Williamsburg")."""
    if not title:
        return None
    head = title
    for sep in _TITLE_SEPARATORS:
        head = head.split(sep, 1)[0]
    return head.strip() or None


def merge_profile(extracted: dict, place_id: str | None) -> dict:
    """Website/owner facts + the one Places field we're allowed to keep (R9)."""
    return {**extracted, "place_id": place_id}
