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
