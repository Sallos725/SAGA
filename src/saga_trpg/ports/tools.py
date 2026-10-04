from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue

from saga_trpg.domain.tools import ToolCall, ToolExecution


class ToolIdentityConflict(ValueError):
    """The same logical call identity was reused with different inputs or metadata."""


class ToolExecutionPending(RuntimeError):
    """Execution is in flight or ambiguous; automatic redispatch is forbidden."""


class ToolPort(Protocol):
    def execute(self, call: ToolCall, *, idempotency_key: str) -> JsonValue:
        """Execute at the external boundary; forward the key if the remote supports it."""
        ...


class ToolResultTapePort(Protocol):
    def get(self, call: ToolCall) -> ToolExecution | None: ...

    def execute(self, call: ToolCall, tool: ToolPort) -> ToolExecution:
        """Durably claim before execution, persist before return, never retry ambiguity."""
        ...


@dataclass(frozen=True)
class ToolBinding:
    name: str
    version: str
    protocol_version: str
    executor: ToolPort
