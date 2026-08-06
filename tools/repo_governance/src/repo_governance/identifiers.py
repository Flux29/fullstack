"""Stable identifier rules.

Identifiers outlive the paths they describe: a component keeps its ID when its directory
moves, so a file move is a graph identity update rather than a new unrelated component.
Every pattern here is also enforced in the JSON Schemas, which are the committed contract;
these constants exist so the CLI produces IDs the schemas already accept.
"""

from __future__ import annotations

import re
import unicodedata

#: Component, validator, finding, exception, and policy-rule identifiers.
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: Architecture decision records.
ADR_PATTERN = re.compile(r"^ADR-\d{3}$")

#: Change records: an ISO date followed by a slug.
CHANGE_RECORD_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]{0,63}$")

#: ISO calendar dates. Deliberately not a datetime: generated governance files carry no
#: clock time, only the date a human-meaningful event happened.
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def slugify(text: str, *, max_length: int = 64) -> str:
    """Reduce free text to a stable slug matching `SLUG_PATTERN`.

    Deterministic and lossy by design: two different summaries can slug to the same value,
    which is why change-record IDs pair the slug with a date and are checked for collisions
    rather than trusted to be unique on their own.
    """
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    lowered = normalized.lower()
    hyphenated = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    truncated = hyphenated[:max_length].rstrip("-")
    return truncated or "change"


def is_slug(value: str) -> bool:
    return bool(SLUG_PATTERN.match(value))


def is_adr_id(value: str) -> bool:
    return bool(ADR_PATTERN.match(value))


def is_change_record_id(value: str) -> bool:
    return bool(CHANGE_RECORD_PATTERN.match(value))


def is_iso_date(value: str) -> bool:
    return bool(DATE_PATTERN.match(value))


def change_record_id(date: str, summary: str) -> str:
    """Build a change-record ID from a date and a free-text summary."""
    return f"{date}-{slugify(summary)}"
