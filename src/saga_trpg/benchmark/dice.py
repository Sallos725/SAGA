"""Pre-recorded dice for benchmark runs: every model gets the same physical results."""

from __future__ import annotations

from collections import deque

from saga_trpg.domain.models import RollRecord
from saga_trpg.runtime.rng import parse_dice_expression


class TapeDice:
    """Rolls come from a tape keyed by dice expression, consumed in order.

    Unlike seeded dice, the tape does not depend on event ids, so two drivers that choose
    different actions still face the same sequence of physical outcomes per expression.
    An exhausted or unknown expression is an error: a run must declare its tape fully.
    """

    def __init__(self, rolls: dict[str, list[list[int]]]) -> None:
        self._tape = {expression: deque(raws) for expression, raws in rolls.items()}
        self.consumed: list[tuple[str, str, list[int]]] = []

    def roll(self, *, world_id: str, event_id: str, roll_key: str, expression: str) -> RollRecord:
        count, sides, modifier = parse_dice_expression(expression)
        queue = self._tape.get(expression)
        if not queue:
            raise ValueError(f"tape has no remaining roll for {expression!r} ({roll_key})")
        raw = list(queue.popleft())
        if len(raw) != count or any(not 1 <= value <= sides for value in raw):
            raise ValueError(f"tape entry {raw} does not fit {expression!r}")
        self.consumed.append((event_id, roll_key, raw))
        return RollRecord(
            roll_key=roll_key,
            expression=expression,
            raw=raw,
            modifier=modifier,
            total=sum(raw) + modifier,
        )
