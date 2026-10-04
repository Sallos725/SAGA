from __future__ import annotations

from typing import Protocol

from nmos_trpg.domain.events import GameEvent
from nmos_trpg.domain.models import RuleResult, WorldState
from nmos_trpg.ports.dice import DicePort


class RuleModule(Protocol):
    name: str
    version: str

    def apply(self, *, state: WorldState, event: GameEvent, dice: DicePort) -> RuleResult: ...
