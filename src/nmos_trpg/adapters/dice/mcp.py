from __future__ import annotations

from nmos_trpg.domain.models import RollRecord


class MCPDiceAdapter:
    """Placeholder for an MCP dice server.

    Implementation requirements:
    - use a real MCP client only in this adapter layer;
    - map one tool result into RollRecord;
    - never call the tool during narration-only rerenders;
    - journal the RollRecord with the committed event;
    - expose timeout/error behavior explicitly;
    - for benchmark mode, either use a deterministic MCP server/seed or pre-record rolls.
    """

    def __init__(self, server: str) -> None:
        self.server = server

    def roll(self, *, world_id: str, event_id: str, roll_key: str, expression: str) -> RollRecord:
        raise NotImplementedError(
            "Bind this adapter to the selected MCP dice server. Do not implement game logic here."
        )
