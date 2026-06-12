"""UTC time helper.

`datetime.utcnow()` is deprecated. This returns the equivalent *naive* UTC
datetime via a timezone-aware call, so the ISO strings we store keep their
existing offset-free format ("2026-06-12T22:59:57.123456") and stay
string-comparable with rows written before the switch.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
