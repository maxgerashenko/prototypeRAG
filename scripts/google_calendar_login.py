"""One-time Google Calendar consent for the demo calendar, with no service account key.

Run on the Mac, logged in (in the browser it opens) as the Gmail that owns the calendar:

    uv run python scripts/google_calendar_login.py --client-file ~/.config/prototyperag/gcal-oauth-client.json

`--client-file` is the "Desktop app" OAuth client JSON downloaded from Google Cloud. The script
opens the consent page, then saves an authorized-user token file (refresh token) with mode 600,
by default to ~/.config/prototyperag/gcal-token.json. Set `GOOGLE_CALENDAR_TOKEN_FILE` in `.env`
to that path. Both files are secrets: the script refuses to write inside the repository.
"""

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:  # run by path (`python scripts/...`): only scripts/ is on sys.path
    sys.path.insert(0, str(REPO))
DEFAULT_TOKEN = "~/.config/prototyperag/gcal-token.json"


def inside_repo(path: Path) -> bool:
    return path.resolve().is_relative_to(REPO)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--client-file", required=True, help="OAuth client (Desktop app) JSON from Google Cloud")
    ap.add_argument("--token-file", default=DEFAULT_TOKEN, help=f"where to save the token (default {DEFAULT_TOKEN})")
    args = ap.parse_args(argv)

    client_file = Path(args.client_file).expanduser()
    token_file = Path(args.token_file).expanduser()
    if inside_repo(token_file) or inside_repo(client_file):
        print("Refusing: keep the client file and the token outside the repository (they are secrets).", file=sys.stderr)
        return 2
    if not client_file.is_file():
        print(f"Client file not found: {client_file}", file=sys.stderr)
        return 2

    from google_auth_oauthlib.flow import InstalledAppFlow

    from app.actions.booking.google_client import SCOPES

    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), scopes=list(SCOPES))
    # offline + consent: always return a refresh token, even if this client was authorized before
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        print("Google returned no refresh token; remove the app at myaccount.google.com/permissions and retry.", file=sys.stderr)
        return 1

    token_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())
    os.chmod(token_file, 0o600)
    print(f"Saved {token_file}\nSet in .env:  GOOGLE_CALENDAR_TOKEN_FILE={token_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
