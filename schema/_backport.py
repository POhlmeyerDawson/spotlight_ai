"""StrEnum for Python < 3.11.

`schema/events.py` imports this, but the commit that introduced the import never
added the file — it changed events.py alone, so `main` could not import the schema
at all and the whole suite failed at collection.

Python 3.11 added StrEnum to the stdlib. Below that we reproduce the one behaviour
the schema relies on: members compare and serialise as plain strings, so
`str(EventKind.GREEN_FLAG) == "green_flag"` holds on every supported version. Event
kinds are written to the database and matched by string across module boundaries;
if that identity breaks, every kind-filtered query silently returns nothing.
"""

from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # pragma: no cover - exercised only on 3.9/3.10
    from enum import Enum

    class StrEnum(str, Enum):
        """Mirrors enum.StrEnum: value is the string, and str() returns it."""

        def __str__(self) -> str:
            return str(self.value)

        @staticmethod
        def _generate_next_value_(name: str, start: int, count: int, last: list) -> str:
            return name.lower()


__all__ = ["StrEnum"]
