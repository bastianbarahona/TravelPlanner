"""Environment only. A missing variable fails at import, so a misconfigured process never starts.

Importing this opens no connection: the engine in app/db/session.py connects on first use.
"""
import os

DATABASE_URL = os.environ["APP_DATABASE_URL"]
# Same parsing as MOCK_TOKENS in provider-mock/main.py. These pairs are the test identities: there is no users table.
TOKENS = dict(pair.split(":", 1) for pair in os.environ["APP_TOKENS"].split(","))
IDENTITIES = set(TOKENS.values())
