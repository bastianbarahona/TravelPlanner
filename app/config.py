"""Environment only. A missing variable fails at import, so a misconfigured process never starts.

Importing this opens no connection: the engine in app/db/session.py connects on first use.
"""
import os

DATABASE_URL = os.environ["APP_DATABASE_URL"]
# Same parsing as MOCK_TOKENS in provider-mock/main.py. These pairs are the test identities: there is no users table.
TOKENS = dict(pair.split(":", 1) for pair in os.environ["APP_TOKENS"].split(","))
IDENTITIES = set(TOKENS.values())

# The provider is the only service the application calls (Section 3 of docs/CONTRACT_NOTES.md). The URL includes /v1.
PROVIDER_BASE_URL = os.environ["PROVIDER_BASE_URL"]
PROVIDER_TOKEN = os.environ["PROVIDER_TOKEN"]
PROVIDER_TIMEOUT_SECONDS = float(os.environ.get("PROVIDER_TIMEOUT_SECONDS", "10"))

# Documents (Iteration 5). "0" leaves every document as stored. The render delay exists for tests only.
DOCUMENT_WORKER_ENABLED = os.environ.get("DOCUMENT_WORKER_ENABLED", "1") != "0"
DOCUMENT_LEASE_SECONDS = float(os.environ.get("DOCUMENT_LEASE_SECONDS", "30"))
DOCUMENT_RENDER_DELAY_SECONDS = float(os.environ.get("DOCUMENT_RENDER_DELAY_SECONDS", "0"))

# Reconciliation of unknown and pending bookings (CONTRACT_NOTES A15). Seconds before the 1st, 2nd, ... check; the last repeats.
BOOKING_POLL_INTERVALS = [float(seconds) for seconds in os.environ.get("BOOKING_POLL_INTERVALS", "5,10,30,60").split(",")]
if not all(0 < seconds < 86400 for seconds in BOOKING_POLL_INTERVALS):  # also refuses nan and inf
    raise ValueError("BOOKING_POLL_INTERVALS must be seconds above 0 and below 86400, such as 5,10,30,60")
BOOKING_POLL_ENABLED = os.environ.get("BOOKING_POLL_ENABLED", "1") != "0"  # 0: tests that must see the stored state untouched

# Assistant (Iteration 6). deterministic: no network and no key. anthropic: the model, with credentials from the SDK's own
# resolution (ANTHROPIC_API_KEY). Any other value stops the process at import, like every setting above.
ASSISTANT_MODE = os.environ.get("ASSISTANT_MODE", "deterministic")
if ASSISTANT_MODE not in ("deterministic", "anthropic"):
    raise ValueError("ASSISTANT_MODE must be deterministic or anthropic")
ASSISTANT_MODEL = os.environ.get("ASSISTANT_MODEL", "claude-sonnet-5-5")
