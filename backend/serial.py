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
