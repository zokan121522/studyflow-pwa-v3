"""Wire-format helpers shared by the models and routes.

Why this exists
---------------
Under Postgres, psycopg2 hands back real ``datetime`` and ``date`` objects
for timestamp and date columns, so the serialisers have always called
``.isoformat()`` on them. SQLite has no date type: every timestamp is TEXT,
and the stdlib driver returns a ``str``. The same expression that works on
one engine raises ``'str' object has no attribute 'isoformat'`` on the
other.

Rather than sprinkle ``hasattr`` checks through thirty call sites, the
conversion goes through :func:`iso`. It is a no-op for text, which is what
makes it safe to apply unconditionally: under Postgres the value is a
datetime and comes back formatted; under SQLite it is already the ISO
string the column stores and is returned untouched.
"""

from __future__ import annotations

import datetime
from typing import Any, Optional


def iso(value: Any) -> Optional[str]:
    """Return ``value`` as an ISO-8601 string, or None.

    Accepts what either engine can produce for a temporal column:
    ``datetime``/``date`` (Postgres), ``str`` (SQLite), or None.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    isoformat = getattr(value, "isoformat", None)
    if isoformat is None:
        return str(value)
    return isoformat()


def as_datetime(value: Any) -> Any:
    """Coerce a temporal column value into a datetime, or None.

    :func:`iso` is the right tool for *formatting* a value, because returning
    the stored text unchanged is exactly correct. This is for the rarer case
    where the value has to be *computed on* -- an age in hours, a comparison,
    arithmetic. That cannot be done on text, so the SQLite string is parsed.

    Naive values are assumed to be UTC, which is what the app stores: SQLite
    has no time zones, and the compat layer writes ``utcnow_iso()``. A value
    that is already aware is left alone, and an unparseable string is
    returned as-is so the caller's own error surfaces rather than a
    fabricated datetime.
    """
    if value is None or isinstance(value, datetime.datetime):
        return value
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        for parse in (datetime.datetime.fromisoformat,):
            try:
                parsed = parse(text)
            except ValueError:
                continue
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed
    return value
