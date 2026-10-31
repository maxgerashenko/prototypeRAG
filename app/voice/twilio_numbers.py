"""The Twilio numbers owned by the account (issue #147), for assigning one to a business.

Only reads `IncomingPhoneNumbers` — buying numbers is out of scope (monthly rental, DEC-34).
`get_client()` is the one place that talks to Twilio, so tests replace it with a fake.
"""

from dataclasses import dataclass

from app.config import get_settings


class TwilioNotConfigured(Exception):
    """No account SID / auth token (the cloud box and CI have none)."""


@dataclass(frozen=True)
class AccountNumber:
    number: str  # E.164
    label: str  # Twilio's friendly name


def get_client():
    s = get_settings()
    if not (s.twilio_account_sid and s.twilio_auth_token):
        raise TwilioNotConfigured("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN are not set")
    from twilio.rest import Client
    return Client(s.twilio_account_sid, s.twilio_auth_token)


def list_account_numbers() -> list[AccountNumber]:
    """The account's incoming numbers, sorted. Raises TwilioNotConfigured or a Twilio error."""
    found = get_client().incoming_phone_numbers.list()
    return sorted(
        (AccountNumber(number=n.phone_number, label=n.friendly_name or n.phone_number) for n in found),
        key=lambda n: n.number,
    )
