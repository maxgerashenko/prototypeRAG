"""Guest identity for bookings (session_guests). The name the caller gives is the identity
(`name_key`); the phone only tells two people with the same name apart. Each guest has a fake
`@guest.invalid` email (RFC 2606, never resolves) that is written as the Google event attendee
and never changes, even when the spelling of the name is fixed later.

Nothing is merged on a guess: a near-match ("Katherine"/"Catherine") is a new guest and the
owner merges them. Callers hold the business's transaction; these functions only add rows."""

import re
import unicodedata
import uuid

from sqlalchemy import select

from app.db.models import SessionGuest

DOMAIN = "guest.invalid"
LOCAL_MAX = 50
_EXTRA = str.maketrans({"ł": "l", "ø": "o", "đ": "d", "ð": "d", "þ": "th"})
_LIGATURES = {"ß": "ss", "æ": "ae", "œ": "oe"}


def _ascii(text: str) -> str:
    """Lower-case ASCII of `text`: accents dropped, letters that don't decompose mapped."""
    text = text.lower()
    for k, v in _LIGATURES.items():
        text = text.replace(k, v)
    text = text.translate(_EXTRA)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def name_key(display_name: str) -> str:
    """Matching key: ASCII, lower case, words separated by one space. "" for a name with no
    Latin letters (Cyrillic, Chinese)."""
    return " ".join(re.findall(r"[a-z0-9]+", _ascii(display_name)))


def email_base(display_name: str, guest_id: uuid.UUID) -> str:
    """Local part of the fake email: name words joined with `_`, cut to 50 characters;
    `guest_<8 hex of id>` when nothing Latin is left."""
    local = "_".join(name_key(display_name).split())[:LOCAL_MAX].strip("_")
    return local or f"guest_{guest_id.hex[:8]}"


def _digits(phone: str | None) -> str:
    return re.sub(r"\D", "", phone or "")


def split_name(name: str) -> tuple[str, str]:
    """"Anna Maria Kowalska" -> ("Anna Maria", "Kowalska"): the last word is the last name."""
    first, _, last = " ".join(name.split()).rpartition(" ")
    return (first, last) if first else (last, "")


def display_name(first: str, last: str, phone: str | None) -> str:
    """"First Last"; with no last name, the first name plus the last 4 phone digits."""
    first, last = " ".join(first.split()), " ".join(last.split())
    if last:
        return f"{first} {last}"
    tail = _digits(phone)[-4:]
    return f"{first} {tail}" if tail else first


def _same_phone(a: str | None, b: str | None) -> bool:
    """True when either side has no phone, or both end in the same 10 digits."""
    da, db = _digits(a), _digits(b)
    return not da or not db or da[-10:] == db[-10:]


def _free_email(session, business_id: uuid.UUID, base: str) -> str:
    taken = set(
        session.scalars(
            select(SessionGuest.email).where(
                SessionGuest.business_id == business_id, SessionGuest.email.like(f"{base}%@{DOMAIN}")
            )
        )
    )
    email, n = f"{base}@{DOMAIN}", 1
    while email in taken:
        n += 1
        email = f"{base}_{n}@{DOMAIN}"
    return email


def find_or_create(
    session, business_id: uuid.UUID, first: str, last: str = "", phone: str | None = None
) -> SessionGuest:
    """The guest for this name in this business, made if missing. Same name key and a phone that
    is the same or missing on either side is the same guest; a different known phone is a new
    guest with a numbered email (john_smith_2). Merged-away guests are never matched."""
    name = display_name(first, last, phone)
    key = name_key(name)
    if not key:  # nothing Latin to match on: always a new guest
        candidates = []
    else:
        candidates = session.scalars(
            select(SessionGuest)
            .where(
                SessionGuest.business_id == business_id,
                SessionGuest.name_key == key,
                SessionGuest.merged_into.is_(None),
            )
            .order_by(SessionGuest.created_at, SessionGuest.id)
        ).all()
    for guest in candidates:
        if _same_phone(guest.phone, phone):
            return guest
    guest_id = uuid.uuid4()
    guest = SessionGuest(
        id=guest_id,
        business_id=business_id,
        display_name=name,
        name_key=key,
        email=_free_email(session, business_id, email_base(name, guest_id)),
        phone=phone or None,
    )
    session.add(guest)
    session.flush()
    return guest
