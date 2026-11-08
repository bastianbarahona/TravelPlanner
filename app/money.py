"""Money boundary: strings with exactly two decimals in, Decimal inside, the same string out.

This is the only place in app/ that rounds (CLAUDE.md rule 2): format_amount, half up.
"""
import re
from decimal import ROUND_HALF_UP, Decimal

# Up to 10 integer digits, so NUMERIC(12,2) holds every accepted value (A44).
AMOUNT = re.compile(r"(0|[1-9][0-9]{0,9})\.[0-9]{2}")


def parse_amount(text):
    if not AMOUNT.fullmatch(text):
        raise ValueError("Use digits with exactly two decimals, such as 420.10, up to 9999999999.99.")
    return Decimal(text)


def format_amount(amount):
    return format(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")
