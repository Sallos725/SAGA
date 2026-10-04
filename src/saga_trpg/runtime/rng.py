from __future__ import annotations

import hashlib
import random
import re

from saga_trpg.domain.models import RollRecord

_DICE = re.compile(r"^(?P<count>\d*)d(?P<sides>\d+)(?P<mod>[+-]\d+)?$")


class DeterministicDice:
    """Development/reference dice implementation.

    It is deterministic for a stable (seed, world, event, roll_key, expression) tuple.
    Production play may use an MCP-backed roller, but the resulting RollRecord must still be
    journaled and replayed rather than re-rolled during narration regeneration.
    """

    def __init__(self, seed: str = "dev-seed") -> None:
        self.seed = seed

    def roll(self, *, world_id: str, event_id: str, roll_key: str, expression: str) -> RollRecord:
        match = _DICE.fullmatch(expression.replace(" ", ""))
        if not match:
            raise ValueError(f"unsupported dice expression: {expression}")

        count = int(match.group("count") or "1")
        sides = int(match.group("sides"))
        modifier = int(match.group("mod") or "0")
        if count < 1 or count > 100 or sides < 2 or sides > 100000:
            raise ValueError("dice expression out of supported bounds")

        material = "|".join([self.seed, world_id, event_id, roll_key, expression])
        digest = hashlib.sha256(material.encode("utf-8")).digest()
        rng = random.Random(int.from_bytes(digest, "big"))
        raw = [rng.randint(1, sides) for _ in range(count)]
        return RollRecord(
            roll_key=roll_key,
            expression=expression,
            raw=raw,
            modifier=modifier,
            total=sum(raw) + modifier,
        )
