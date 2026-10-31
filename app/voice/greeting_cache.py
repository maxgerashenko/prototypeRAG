"""Pre-generated call greeting audio (V6, DEC-17).

The standard greeting is synthesized once -- when a crawl finishes, or on the first call
that misses -- and stored in Postgres (`greeting_audio`), so a call starts playing without
a TTS round trip, also after a restart. A row is keyed by everything that changes the
audio: business, text, sample rate, voice and language, so a renamed business or a changed
`TTS_VOICE` is just a miss that fills itself on the next call (or the next crawl).
"""

import logging
import re
import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.db import tenant_session
from app.db.models import Business, BusinessProfile, GreetingAudio
from app.ingest.profile import is_placeholder_name
from app.voice.audio import TWILIO_SAMPLE_RATE

log = logging.getLogger(__name__)

BROWSER_SAMPLE_RATE = 16000  # same as app/voice/browser_ws.py; not imported, it imports the session
SAMPLE_RATES = (TWILIO_SAMPLE_RATE, BROWSER_SAMPLE_RATE)

MAX_GREETING_LEN = 300  # the owner's part (DEC-53)
AI_DISCLOSURE = "Hi, I am an AI assistant."  # always the first sentence of a call (DEC-17, DEC-53)

# the disclosure in the ways an owner might type it at the start of their text
_DISCLOSURE_START = re.compile(r"^\s*(?:(?:hi|hello|hey)\b[\s,!.]*)?i(?:\s+a|')m\s+an?\s+(?:ai|a\.i\.)\s+assistant\b[\s.,!:;-]*", re.IGNORECASE)


def greeting_text(name: str) -> str:
    # AI disclosure always (DEC-17); "may be recorded" only once recording exists (DEC-22)
    return f"Hi, you've reached {name}. I'm an AI assistant. How can I help?"


def business_greeting(business: Business, profile: BusinessProfile | None) -> str:
    """The full spoken opening (DEC-53): the fixed AI disclosure + the owner's text from
    `settings.greeting` (nothing more while it is empty). A copy of the disclosure the owner
    typed ("I'm an AI assistant", "Hello, I am an AI assistant.", ...) at the start of their text is dropped, so it is never said twice. `profile` is unused for now."""
    owner = (business.settings or {}).get("greeting")
    owner = owner.strip() if isinstance(owner, str) else ""
    owner = _DISCLOSURE_START.sub("", owner, count=1).strip()
    return f"{AI_DISCLOSURE} {owner}" if owner else AI_DISCLOSURE


def voice_name(business: Business, profile: BusinessProfile | None) -> str:
    """The name the assistant says: the brand (A3, plan/07-knowledge-quality.md §3), else
    the profile's name while the brand is still a placeholder."""
    if not is_placeholder_name(business.name):
        return business.name
    return (profile.name if profile and profile.name else None) or business.name


def load(business_id: uuid.UUID, text: str, sample_rate: int, voice: str, language: str) -> bytes | None:
    with tenant_session(business_id) as session:
        row = session.get(GreetingAudio, (business_id, text, sample_rate, voice, language))
        return bytes(row.audio) if row is not None else None


def store(business_id: uuid.UUID, text: str, sample_rate: int, voice: str, language: str, audio: bytes) -> None:
    stmt = insert(GreetingAudio).values(
        business_id=business_id, text=text, sample_rate=sample_rate, voice=voice, language=language, audio=audio
    ).on_conflict_do_nothing()
    with tenant_session(business_id) as session:
        session.execute(stmt)


def pregenerate(
    business_id: uuid.UUID, synthesize: Callable[[str, int], bytes] | None = None, rates: tuple[int, ...] = SAMPLE_RATES
) -> int:
    """Synthesize and store the standard greeting at each call sample rate; returns how many
    were newly generated. Needs Google TTS credentials -- callers treat a failure as
    best effort, the first call then fills the cache."""
    if synthesize is None:
        from app.voice import tts

        synthesize = tts.synthesize
    settings = get_settings()
    with tenant_session(business_id) as session:
        business = session.get(Business, business_id)
        if business is None:
            raise LookupError(f"unknown business {business_id}")
        text = business_greeting(business, session.get(BusinessProfile, business_id))
    made = 0
    for rate in rates:
        if load(business_id, text, rate, settings.tts_voice, settings.voice_language) is None:
            store(business_id, text, rate, settings.tts_voice, settings.voice_language, synthesize(text, rate))
            made += 1
    return made


def cache_status(
    business_id: uuid.UUID, text: str, rates: tuple[int, ...] = SAMPLE_RATES
) -> tuple[bool, datetime | None]:
    """Whether `text` is stored for every sample rate with the current voice and language,
    and when the newest row was made."""
    settings = get_settings()
    with tenant_session(business_id) as session:
        stamps = session.scalars(
            select(GreetingAudio.created_at).where(
                GreetingAudio.business_id == business_id,
                GreetingAudio.text == text,
                GreetingAudio.voice == settings.tts_voice,
                GreetingAudio.language == settings.voice_language,
                GreetingAudio.sample_rate.in_(rates),
            )
        ).all()
    cached = len(stamps) == len(set(rates))  # primary key: one row per rate
    return cached, (max(stamps) if stamps else None)


def prune(business_id: uuid.UUID, keep_text: str) -> int:
    """Delete this business's cached *opening* greetings (texts starting with the disclosure) other
    than `keep_text`; the cached prompts ("Are you still there?"...) stay. Returns the count."""
    with tenant_session(business_id) as session:
        result = session.execute(
            delete(GreetingAudio).where(GreetingAudio.business_id == business_id, GreetingAudio.text != keep_text,
                GreetingAudio.text.startswith(AI_DISCLOSURE),
            )
        )
        return result.rowcount


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Pre-generate a business's greeting audio")
    parser.add_argument("--business-id", type=uuid.UUID, required=True)
    args = parser.parse_args()
    print(f"generated: {pregenerate(args.business_id)}")


if __name__ == "__main__":
    main()
