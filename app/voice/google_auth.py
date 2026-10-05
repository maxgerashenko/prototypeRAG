"""One Application Default Credentials check per process for Google STT/TTS (V7).

Without credentials, `google.auth.default()` ends by probing the GCE metadata server,
which takes ~3 s to time out. The STT/TTS client getters are `lru_cache`d, but a cache
doesn't keep exceptions, so every turn used to pay those 3 s again before failing. Here
the outcome is cached either way, and `warm_up()` runs the check at app startup so even
the first call fails fast with a clear message.
"""

import functools
import logging
import threading

import google.auth
from google.auth.exceptions import DefaultCredentialsError

log = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _problem() -> str | None:
    try:
        google.auth.default()
    except DefaultCredentialsError as exc:
        return str(exc)
    return None


def require_credentials() -> None:
    problem = _problem()
    if problem is not None:
        raise RuntimeError(
            "Google credentials missing -- run `gcloud auth application-default login` "
            f"and restart the server ({problem})"
        )


def warm_up() -> None:
    """Run the check in the background at startup; only logs, never blocks or raises."""

    def check() -> None:
        problem = _problem()
        if problem is not None:
            log.warning("Google credentials missing, voice calls will fail: %s", problem)

    threading.Thread(target=check, daemon=True).start()
