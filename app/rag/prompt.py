"""Prompt builder (plan/02-local-rag.md "Answer pipeline" / "Prompt rules")."""

from app.db.models import BusinessProfile
from app.rag.retrieve import RetrievedChunk

SYSTEM_PROMPT = (
    "Answer only from the provided business profile and retrieved chunks below. "
    "Never use outside knowledge about this business or any other.\n"
    "If the answer is not in the profile or chunks, state that plainly and offer "
    "to take a message or transfer to a human.\n"
    "Keep answers short, as they will be spoken aloud.\n"
    "Answer in the same language the user is asking in."
)


def format_profile(profile: BusinessProfile | None) -> str:
    """Known facts, one per line, skipping unset fields. No internal ids (business_id/place_id)."""
    if profile is None:
        return "No business profile available."
    lines = []
    if profile.name:
        lines.append(f"Name: {profile.name}")
    if profile.address:
        lines.append(f"Address: {profile.address}")
    if profile.phone:
        lines.append(f"Phone: {profile.phone}")
    if profile.email:
        lines.append(f"Email: {profile.email}")
    if profile.opening_hours:
        lines.append(f"Hours: {profile.opening_hours}")
    if profile.booking_policy:
        lines.append(f"Booking policy: {profile.booking_policy}")
    if profile.price_range:
        lines.append(f"Price range: {profile.price_range}")
    if profile.languages:
        lines.append(f"Languages: {', '.join(profile.languages)}")
    return "\n".join(lines) if lines else "No business profile available."


def format_chunks(chunks: list[RetrievedChunk]) -> str:
    """Retrieved chunks, numbered, with their section heading and text."""
    if not chunks:
        return "No matching information found in the knowledge base."
    parts = [f"[{i}] {c.section_heading or '(no heading)'}\n{c.text}" for i, c in enumerate(chunks, start=1)]
    return "\n\n".join(parts)


def build_prompt(
    profile: BusinessProfile | None, chunks: list[RetrievedChunk], history: list[dict], question: str
) -> list[dict]:
    """System message (rules + profile + chunks) + history + the new question."""
    system_content = f"{SYSTEM_PROMPT}\n\n{format_profile(profile)}\n\n{format_chunks(chunks)}"
    return [{"role": "system", "content": system_content}, *history, {"role": "user", "content": question}]
