"""Pre-generated call greeting audio (V6, DEC-17).

The standard greeting is synthesized once -- when a crawl finishes, or on the first call
that misses -- and stored in Postgres (`greeting_audio`), so a call starts playing without
a TTS round trip, also after a restart. A row is keyed by everything that changes the
audio: business, text, sample rate, voice and language, so a renamed business or a changed
`TTS_VOICE` is just a miss that fills itself on the next call (or the next crawl).
"""

import logging
import uuid
from collections.abc import Callable

from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.db import tenant_session
from app.db.models import Business, BusinessProfile, GreetingAudio
from app.ingest.profile import is_placeholder_name
from app.voice.audio import TWILIO_SAMPLE_RATE

log = logging.getLogger(__name__)

BROWSER_SAMPLE_RATE = 16000  # same as app/voice/browser_ws.py; not imported, it imports the session
SAMPLE_RATES = (TWILIO_SAMPLE_RATE, BROWSER_SAMPLE_RATE)


def greeting_text(name: str) -> str:
    # AI disclosure always (DEC-17); "may be recorded" only once recording exists (DEC-22)
    return f"Hi, you've reached {name}. I'm an AI assistant. How can I help?"


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
        text = greeting_text(voice_name(business, session.get(BusinessProfile, business_id)))
    made = 0
    for rate in rates:
        if load(business_id, text, rate, settings.tts_voice, settings.voice_language) is None:
            store(business_id, text, rate, settings.tts_voice, settings.voice_language, synthesize(text, rate))
            made += 1
    return made


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Pre-generate a business's greeting audio")
    parser.add_argument("--business-id", type=uuid.UUID, required=True)
    args = parser.parse_args()
    print(f"generated: {pregenerate(args.business_id)}")


if __name__ == "__main__":
    main()
