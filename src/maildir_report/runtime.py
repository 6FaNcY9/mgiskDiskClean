"""
runtime.py — Deterministic report timestamp handling for maildir_report.

Design rules
------------
- NO datetime.now() calls.  Callers must supply an explicit timestamp string.
- The canonical input format is ISO 8601 (with or without timezone offset).
- All returned datetimes are timezone-aware UTC objects.
- A naive input (no UTC offset) is treated as UTC (not local time).
- Raises ValueError on any unparseable input so callers fail loudly.

Public API
----------
parse_report_timestamp(ts_str: str) -> datetime
    Parse an ISO 8601 datetime string and return a UTC-aware datetime.
    Accepts strings with offset ("+02:00", "-05:00", "Z"), and naive strings
    (assumed UTC).  Date-only strings ("2024-03-20") are rejected because they
    are ambiguous and cannot represent a precise point-in-time for a report.

format_report_timestamp(dt: datetime) -> str
    Format a UTC-aware datetime as the canonical ISO 8601 string used in
    PDF metadata and manifest fields: "YYYY-MM-DDTHH:MM:SS+00:00".
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_report_timestamp(ts_str: str) -> datetime:
    """Parse an ISO 8601 datetime string and return a UTC-aware datetime.

    Parameters
    ----------
    ts_str:
        An ISO 8601 string such as:
        - ``"2024-06-15T10:00:00+00:00"``  (explicit UTC)
        - ``"2024-06-15T10:00:00+02:00"``  (positive offset, normalised to UTC)
        - ``"2024-06-15T10:00:00-05:00"``  (negative offset, normalised to UTC)
        - ``"2024-06-15T10:00:00Z"``        (Z suffix, treated as UTC)
        - ``"2024-06-15T10:00:00"``         (naive, assumed UTC)

    Returns
    -------
    datetime
        A timezone-aware datetime in UTC (``tzinfo == datetime.timezone.utc``).

    Raises
    ------
    ValueError
        If *ts_str* is empty, a date-only string (no time component), or
        otherwise unparseable as an ISO 8601 datetime.
    """
    if not ts_str:
        raise ValueError(
            "Empty timestamp string — supply an explicit ISO 8601 datetime"
        )

    # Normalise the 'Z' suffix to '+00:00' so fromisoformat handles it (Python < 3.11
    # does not support the 'Z' suffix in fromisoformat, so we normalise for safety).
    normalised = ts_str.strip()
    if normalised.endswith("Z"):
        normalised = normalised[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise ValueError(
            f"Cannot parse {ts_str!r} as an ISO 8601 datetime: {exc}"
        ) from exc

    # Reject date-only values: they have no time component and are not useful
    # as report timestamps (we cannot tell the hour/minute of generation).
    if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and "T" not in ts_str:
        raise ValueError(
            f"Date-only string {ts_str!r} is not a valid report timestamp — "
            "include a time component (e.g. 'T00:00:00')"
        )

    # Attach UTC tzinfo to naive datetimes.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        # Normalise any offset to UTC.
        dt = dt.astimezone(timezone.utc)

    return dt


def format_report_timestamp(dt: datetime) -> str:
    """Format a UTC-aware datetime as the canonical report timestamp string.

    The output is always ``"YYYY-MM-DDTHH:MM:SS+00:00"`` — the ISO 8601
    format used in PDF metadata and manifest ``generated_at`` fields.

    Parameters
    ----------
    dt:
        A timezone-aware datetime (should be UTC; any tz is first normalised).

    Returns
    -------
    str
        Canonical ISO 8601 string with explicit UTC offset ``+00:00``.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
