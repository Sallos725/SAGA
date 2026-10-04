import sqlite3

from saga_trpg.adapters.storage.tool_tape import SQLiteToolResultTape
from saga_trpg.domain.models import RollRecord
from saga_trpg.domain.tools import ToolCall
from saga_trpg.ports.tools import ToolBinding, ToolExecutionPending


class ToolResultRequired(Exception):
    """Suspend speculative rule evaluation; only the outer repository executes the call."""

    def __init__(self, call: ToolCall) -> None:
        self.call = call
        super().__init__("a durable external tool result is required")


class RecordedDice:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        branch_id: str,
        binding: ToolBinding,
        context_hash: str,
    ) -> None:
        self.connection = connection
        self.branch_id = branch_id
        self.binding = binding
        self.context_hash = context_hash
        self.consumed_keys: set[str] = set()

    def roll(self, *, world_id: str, event_id: str, roll_key: str, expression: str) -> RollRecord:
        call = ToolCall(
            world_id=world_id,
            branch_id=self.branch_id,
            event_id=event_id,
            tool_call_key=roll_key,
            tool_name=self.binding.name,
            tool_version=self.binding.version,
            protocol_version=self.binding.protocol_version,
            arguments={"roll_key": roll_key, "expression": expression},
            context_hash=self.context_hash,
        )
        saved = SQLiteToolResultTape.read(self.connection, call)
        if saved is None:
            raise ToolResultRequired(call)
        if saved.status == "pending":
            raise ToolExecutionPending("tool execution is in flight or ambiguous")
        result = RollRecord.model_validate(saved.result, strict=True)
        if result.roll_key != roll_key or result.expression != expression:
            raise ValueError("external dice result does not match the requested roll")
        self.consumed_keys.add(roll_key)
        return result
