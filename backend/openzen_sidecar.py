"""openzen_sidecar.py — local OpenZen (opencode serve) sidecar env bridge.

The StudyFlow PWA talks to a local `opencode serve` sidecar (raw HTTP,
port 54321 by default). On a single-user machine that sidecar is started by
a Windows scheduled task / boot script with FIXED Basic-auth credentials,
which the boot script writes to:

    ~/.studyflow/opencode_sidecar.env

This module lets the backend pick those credentials up WITHOUT requiring
the user to hand-configure env vars. Resolution order (never overrides a
real environment):

    real os.environ  >  ~/.studyflow/opencode_sidecar.env  >  module defaults

`apply_sidecar_env()` is a no-op when the file is missing (e.g. the
Compose sidecar or a remote `opencode serve` deployment), so those setups
keep using OPENCODE_SERVER_URL / OPENCODE_SERVER_USERNAME /
OPENCODE_SERVER_PASSWORD exactly as before.
"""

import logging
import os

logger = logging.getLogger(__name__)

#: Where the local sidecar boot script drops its credentials
#: (see StudyFlow Windows distribution: .studyflow\opencode_sidecar_boot.cmd).
SIDECAR_ENV_FILE = os.path.join(
    os.path.expanduser("~"), ".studyflow", "opencode_sidecar.env"
)

_APPLIED = False


def apply_sidecar_env() -> None:
    """Load ~/.studyflow/opencode_sidecar.env into os.environ (setdefault).

    Idempotent. Missing file / unreadable file / empty lines are ignored.
    Existing environment variables are NOT overwritten, so a deployment
    that exports OPENCODE_SERVER_* keeps control.
    """
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True
    try:
        with open(SIDECAR_ENV_FILE, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and value:
                    os.environ.setdefault(key, value)
    except OSError as exc:
        logger.debug("openzen sidecar env missing/unreadable: %s", exc)