import re
from datetime import UTC, date, datetime, timedelta

MAX_DESTINATION = 200  # the contract's destination.maxLength
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def clean_destination(name):
    """Trim, keep the user's casing. A NUL or a lone surrogate would reach Postgres as a 500."""
    if "\x00" in name:
        raise ValueError("Remove the NUL character.")
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Use valid Unicode text.") from None
    name = name.strip()
    if not name:
        raise ValueError("Enter a destination name.")
    if len(name) > MAX_DESTINATION:
        raise ValueError(f"Use at most {MAX_DESTINATION} characters.")
    return name


def parse_date(text):
    # A plain `date` field accepts epoch numbers and datetimes, so the shape is checked first.
    if not DATE.fullmatch(text):
        raise ValueError("Use a calendar date written YYYY-MM-DD, such as 2030-06-10.")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError("Use a real calendar date, such as 2030-06-10.") from None


def parse_instant(text):
    """Contract-valid date-times may carry a lowercase t or z, which fromisoformat refuses. Compare the result, never the string."""
    return datetime.fromisoformat(text.upper())


def date_errors(starts_on, ends_on, *, check_start):
    """A36. Exactly yesterday in UTC is accepted, so a traveller ahead of UTC can still start today."""
    errors = []
    if check_start and starts_on < datetime.now(UTC).date() - timedelta(days=1):
        errors.append({"field": "startsOn", "message": "The start date cannot be in the past."})
    if ends_on < starts_on:
        errors.append({"field": "body", "message": "endsOn must not be before startsOn."})
    return errors
