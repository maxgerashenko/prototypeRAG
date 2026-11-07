"""One-off backfill: give every confirmed or past booking without a guest one (plan/feature-guest-identity.md,
step 6). Bookings with the same name (and phone) share a guest via `guests.find_or_create`.

    uv run python -m app.actions.booking.backfill_guests [--apply] [--business ID]

Default is a dry run: it prints what it would do and rolls everything back. Safe to run twice:
a booking that has a guest is never touched. Run it before the sync's attendee comparison is on."""

import argparse
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.actions.booking import guests
from app.db import tenant_session
from app.db.models import Booking, Business, SessionGuest
from app.db.session import SessionLocal


class _DryRun(Exception):
    """Raised at the end of a dry run so the business's transaction rolls back."""


def mask_phone(phone: str | None) -> str:
    """"***2284": only the last 4 digits."""
    tail = guests._digits(phone)[-4:]
    return f"***{tail}" if tail else "-"


def backfill_business(business_id: uuid.UUID, apply: bool, out=print) -> tuple[int, int]:
    """Returns (bookings updated, guests created). With apply=False nothing is written."""
    updated = created = 0
    try:
        with tenant_session(business_id) as s:
            known = set(s.scalars(select(SessionGuest.id).where(SessionGuest.business_id == business_id)))
            rows = s.scalars(
                select(Booking)
                .where(
                    Booking.business_id == business_id,
                    Booking.guest_id.is_(None),
                    or_(Booking.status == "confirmed", Booking.starts_at < datetime.now(timezone.utc)),
                )
                .order_by(Booking.starts_at, Booking.id)
            ).all()
            for b in rows:
                first, last = guests.split_name(b.name or "")
                g = guests.find_or_create(s, business_id, first or "Caller", last, b.phone)
                if g.id not in known:
                    known.add(g.id)
                    created += 1
                    out(f"  new guest: {g.display_name} | {mask_phone(g.phone)} | {g.email}")
                b.guest_id = g.id
                if b.caller_conversation_id is None:
                    b.caller_conversation_id = b.conversation_id
                updated += 1
            if not apply:
                raise _DryRun
    except _DryRun:
        pass
    return updated, created


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    ap.add_argument("--business", type=uuid.UUID, help="only this business id")
    args = ap.parse_args(argv)

    if args.business:
        ids = [args.business]
    else:
        with SessionLocal() as s:
            ids = list(s.scalars(select(Business.id).order_by(Business.created_at)))
    print("APPLY" if args.apply else "DRY RUN (nothing is written; pass --apply to write)")
    total_b = total_g = 0
    for bid in ids:
        print(f"business {bid}")
        n, g = backfill_business(bid, args.apply)
        print(f"  bookings to update: {n}, guests to create: {g}")
        total_b += n
        total_g += g
    print(f"total: {total_b} bookings, {total_g} new guests" + ("" if args.apply else " (dry run)"))


if __name__ == "__main__":
    main()
