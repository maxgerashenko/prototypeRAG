"""Review conversations from the terminal: list, show, mark, find junk, delete.

Run: uv run python .claude/tools/review_conversations.py [--business ID] <command>
  list [--status unchecked|verified|in_testing|fixed|all]   (default unchecked)
  show <id-prefix>        transcript + the action_log rows (tool results are not in messages)
  set <id-prefix> <verified|in_testing|fixed|none> [--note TEXT]
  junk                    JUNK (no user message) and REVIEW (looks short/unanswered, may be a bot failure); read-only
  delete <id-prefix>... --yes   refuses if a booking references the conversation
"""

import argparse
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import Text, cast, delete, func, or_, select  # noqa: E402

from app.db import tenant_session  # noqa: E402
from app.db.models import ActionLog, Booking, Conversation, Message, message_order  # noqa: E402

PILOT = "6596a8a2-c563-42c2-b006-b404a02822aa"
JUNK_SECONDS = 15


def find(session, bid, prefix):
    if not re.fullmatch(r"[0-9a-fA-F-]{4,36}", prefix):
        sys.exit(f"{prefix!r}: give at least 4 hex characters of the conversation id")
    rows = session.execute(
        select(Conversation).where(Conversation.business_id == bid, cast(Conversation.id, Text).like(prefix.lower() + "%"))
    ).scalars().all()
    if len(rows) != 1:
        sys.exit(f"{prefix}: {'no match' if not rows else f'{len(rows)} matches, use a longer prefix'}")
    return rows[0]


def local(dt):
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def chat_messages(session, bid, cid=None):
    stmt = select(Message).where(Message.business_id == bid, Message.role.in_(["user", "assistant"]))
    if cid is not None:
        stmt = stmt.where(Message.conversation_id == cid)
    return session.execute(stmt.order_by(*message_order())).scalars().all()


def by_conversation(msgs):
    out = {}
    for m in msgs:
        out.setdefault(m.conversation_id, []).append(m)
    return out


def duration(conv, msgs):
    end = conv.ended_at or (msgs[-1].created_at if msgs else conv.started_at)
    return (end - conv.started_at).total_seconds()


def title(msgs):
    for m in msgs:
        if m.role == "user":
            t = m.content.strip().replace("\n", " ")
            return t[:60] + ("…" if len(t) > 60 else "")
    return "-"


def all_conversations(session, bid):
    convs = session.execute(
        select(Conversation).where(Conversation.business_id == bid).order_by(Conversation.started_at.desc())
    ).scalars().all()
    return convs, by_conversation(chat_messages(session, bid))


def cmd_list(bid, args):
    with tenant_session(bid) as s:
        convs, msgs = all_conversations(s, bid)
        for c in convs:
            if args.status == "unchecked" and c.review_status is not None:
                continue
            if args.status not in ("unchecked", "all") and c.review_status != args.status:
                continue
            m = msgs.get(c.id, [])
            n_user = sum(1 for x in m if x.role == "user")
            print(f"{str(c.id)[:8]}  {local(c.started_at)}  {c.channel:5}  {c.review_status or 'unchecked':10}  {n_user:2} user  {title(m)}")


def cmd_show(bid, args):
    with tenant_session(bid) as s:
        c = find(s, bid, args.id)
        print(f"{c.id}  {c.channel}  started {local(c.started_at)}  review: {c.review_status or 'unchecked'}  {c.review_note or ''}")
        print("--- transcript")
        for m in chat_messages(s, bid, c.id):
            print(f"[{int((m.created_at - c.started_at).total_seconds()):4}s] {m.role}: {m.content}")
        print("--- action_log")
        logs = s.execute(
            select(ActionLog).where(ActionLog.business_id == bid, ActionLog.conversation_id == c.id).order_by(ActionLog.created_at)
        ).scalars().all()
        for a in logs:
            head = a.result.replace("\n", " ")
            print(f"[{int((a.created_at - c.started_at).total_seconds()):4}s] {a.tool} ({a.status}) args={a.arguments}\n        result: {head[:300]}{'…' if len(head) > 300 else ''}")
        if not logs:
            print("(none)")


def cmd_set(bid, args):
    with tenant_session(bid) as s:
        c = find(s, bid, args.id)
        c.review_status = None if args.status == "none" else args.status
        c.review_note = args.note
        c.reviewed_at = None if args.status == "none" else datetime.now(UTC)
        print(f"{str(c.id)[:8]} -> {c.review_status or 'unchecked'}")


def junk_reason(c, msgs):
    """Safe-to-delete reason, or None. Only a call with no user message at all: a caller who
    spoke and got no reply, or hung up fast, is a bot failure and must be reviewed."""
    return None if any(m.role == "user" for m in msgs) else "no user message"


def check_reason(c, msgs):
    """Looks like junk but may be a bot failure: review it, don't delete it."""
    users = [m for m in msgs if m.role == "user"]
    if users and not any(m.role == "assistant" and m.created_at > users[0].created_at for m in msgs):
        return "user spoke, no assistant reply (bot failure?)"
    if len(users) <= 1 and users and duration(c, msgs) <= JUNK_SECONDS:
        return f"<=1 user message and ended within {JUNK_SECONDS}s (hung up on the bot?)"
    return None


def cmd_junk(bid, args):
    with tenant_session(bid) as s:
        convs, msgs = all_conversations(s, bid)
        for c in convs:
            reason = junk_reason(c, msgs.get(c.id, []))
            if reason:
                print(f"{str(c.id)[:8]}  {local(c.started_at)}  JUNK: {reason}")
        for c in convs:
            reason = check_reason(c, msgs.get(c.id, []))
            if reason:
                print(f"{str(c.id)[:8]}  {local(c.started_at)}  REVIEW, do not delete: {reason}")


def cmd_delete(bid, args):
    with tenant_session(bid) as s:
        convs = [find(s, bid, p) for p in args.ids]
        blocked = False
        for c in convs:
            n_msg = s.scalar(select(func.count()).select_from(Message).where(Message.business_id == bid, Message.conversation_id == c.id))
            n_log = s.scalar(select(func.count()).select_from(ActionLog).where(ActionLog.business_id == bid, ActionLog.conversation_id == c.id))
            n_book = s.scalar(
                select(func.count()).select_from(Booking).where(
                    Booking.business_id == bid, or_(Booking.conversation_id == c.id, Booking.caller_conversation_id == c.id)
                )
            )
            if n_book:
                blocked = True
                print(f"{str(c.id)[:8]}  REFUSED: {n_book} booking(s) reference it")
            else:
                print(f"{str(c.id)[:8]}  would delete {n_msg} messages, {n_log} action_log rows (messages_for_owner cascade too)")
        if blocked:
            sys.exit("nothing deleted: remove the refused ids from the command")
        if not args.yes:
            print("dry run; add --yes to delete")
            return
        s.execute(delete(Conversation).where(Conversation.business_id == bid, Conversation.id.in_([c.id for c in convs])))
        print(f"deleted {len(convs)} conversation(s)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--business", default=PILOT)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("list")
    a.add_argument("--status", default="unchecked", choices=["unchecked", "verified", "in_testing", "fixed", "all"])
    a = sub.add_parser("show")
    a.add_argument("id")
    a = sub.add_parser("set")
    a.add_argument("id")
    a.add_argument("status", choices=["verified", "in_testing", "fixed", "none"])
    a.add_argument("--note")
    sub.add_parser("junk")
    a = sub.add_parser("delete")
    a.add_argument("ids", nargs="+")
    a.add_argument("--yes", action="store_true")
    args = p.parse_args()
    bid = uuid.UUID(args.business)
    {"list": cmd_list, "show": cmd_show, "set": cmd_set, "junk": cmd_junk, "delete": cmd_delete}[args.cmd](bid, args)


if __name__ == "__main__":
    main()
