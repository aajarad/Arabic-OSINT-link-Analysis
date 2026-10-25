"""Timestamp helpers. All internal timestamps are timezone-aware UTC ISO-8601."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a trailing 'Z'-style offset."""
    return datetime.now(timezone.utc).isoformat()


def to_iso(dt: datetime) -> str:
    """Serialize a datetime to ISO-8601, assuming UTC if naive."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 string into an aware UTC datetime.

    Tolerates a trailing 'Z' and naive strings (treated as UTC). Returns
    ``None`` for empty/invalid input rather than raising, so that a single
    malformed post never aborts a whole collection run.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        # Try a few common non-ISO layouts before giving up.
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
