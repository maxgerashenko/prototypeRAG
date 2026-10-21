"""Delete every business except the ones named with --keep (id, domain or exact name).

Dry run by default; add --apply to delete. Rows cascade (pages, chunks, conversations, ...).
Run from the repo root against the dev DB:

    uv run python scripts/prune_businesses.py --keep abathhouse.com
    uv run python scripts/prune_businesses.py --keep abathhouse.com --apply
"""

import argparse
import sys

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.db.models import Business


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", action="append", required=True, help="business id, domain or exact name (repeatable)")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: only list)")
    ap.add_argument("--drop-numbered", action="store_true", help="also delete businesses that have phone numbers")
    args = ap.parse_args()

    with SessionLocal() as s, s.begin():
        rows = s.scalars(select(Business).order_by(Business.created_at)).all()
        keep_keys = {k.lower() for k in args.keep}
        keep = [b for b in rows if keep_keys & {str(b.id), (b.domain or "").lower(), b.name.lower()}]
        if not keep:
            sys.exit(f"nothing matches --keep {args.keep}; refusing to delete everything")
        print("KEEP:")
        for b in keep:
            print(f"  {b.id}  {b.name}  {b.domain}  {b.phone_numbers}")
        drop, skipped = [], []
        for b in rows:
            if b in keep:
                continue
            (skipped if b.phone_numbers and not args.drop_numbered else drop).append(b)
        print("DELETE:" if args.apply else "WOULD DELETE (dry run):")
        for b in drop:
            print(f"  {b.id}  {b.name}  {b.domain}  {b.phone_numbers}")
        for b in skipped:
            print(f"  SKIPPED (has phone numbers, use --drop-numbered): {b.id}  {b.name}  {b.phone_numbers}")
        if args.apply and drop:
            s.execute(delete(Business).where(Business.id.in_([b.id for b in drop])))
        elif not args.apply:
            print("dry run -- re-run with --apply to delete")


if __name__ == "__main__":
    main()
