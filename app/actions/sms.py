"""Mock SMS sender (stage 1). Never calls Twilio: it logs the message and records it in the
in-memory `OUTBOX` so tests and the owner screens can see what would have been sent.
#138 swaps `send_sms` for the real Twilio sender (same signature)."""

import logging

log = logging.getLogger(__name__)

OUTBOX_MAX = 200
OUTBOX: list[dict] = []  # newest last; oldest dropped past OUTBOX_MAX


def send_sms(to: str, body: str) -> None:
    """Mock: log and record the message, send nothing."""
    log.info("MOCK SMS to %s: %s", to, body)
    OUTBOX.append({"to": to, "body": body})
    del OUTBOX[:-OUTBOX_MAX]
