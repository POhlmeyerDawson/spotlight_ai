"""Beta-Binomial with forgetting factor lambda. Owner: A.

Wired behind SCORE_MODEL=beta_binomial. Verify the flag works at H10, not H20.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from schema.events import FounderScore


def founder(entity_id: UUID, as_of: datetime) -> FounderScore:
    raise NotImplementedError("A: H8-12")
