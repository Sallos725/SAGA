from __future__ import annotations

from typing import Protocol

from nmos_trpg.domain.models import RollRecord


class DicePort(Protocol):
    def roll(self, *, world_id: str, event_id: str, roll_key: str, expression: str) -> RollRecord:
        """Return a roll for a stable event/roll key.

        The same committed event and roll key must not be re-executed during narration rerolls.
        Runtime/journal code is responsible for replaying stored roll records.
        """
        ...
