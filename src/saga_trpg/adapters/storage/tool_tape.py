import json
import sqlite3
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from saga_trpg.adapters.storage.database import SQLiteDatabase
from saga_trpg.domain.tools import ToolCall, ToolExecution
from saga_trpg.ports.tools import ToolExecutionPending, ToolIdentityConflict, ToolPort
from saga_trpg.runtime.hashing import stable_model_hash, stable_value_hash


def identity(call: ToolCall) -> tuple[str, str, str, str]:
    return call.world_id, call.branch_id, call.event_id, call.tool_call_key


class SQLiteToolResultTape:
    """Durable claims and results; a pending claim is never automatically re-executed."""

    def __init__(self, path: str | Path) -> None:
        self.database = SQLiteDatabase(path)
        self.path = self.database.path

    @staticmethod
    def read(connection: sqlite3.Connection, call: ToolCall) -> ToolExecution | None:
        row = connection.execute(
            """SELECT * FROM tool_executions WHERE world_id = ? AND branch_id = ?
               AND event_id = ? AND tool_call_key = ?""",
            identity(call),
        ).fetchone()
        if row is None:
            return None
        saved = ToolCall.model_validate_json(row["request_json"])
        if (
            stable_model_hash(saved) != row["request_hash"]
            or stable_value_hash(saved.arguments) != row["arguments_hash"]
            or identity(saved) != identity(call)
        ):
            raise ToolIdentityConflict("corrupt tool request identity or hash")
        if stable_model_hash(call) != row["request_hash"]:
            raise ToolIdentityConflict("tool identity reused with different arguments or metadata")
        return ToolExecution(
            call=saved,
            arguments_hash=row["arguments_hash"],
            status=row["status"],
            result=json.loads(row["result_json"]) if row["result_json"] is not None else None,
        )

    def get(self, call: ToolCall) -> ToolExecution | None:
        call = ToolCall.model_validate(call.model_dump())
        with self.database.transaction() as connection:
            return self.read(connection, call)

    def execute(self, call: ToolCall, tool: ToolPort) -> ToolExecution:
        call = ToolCall.model_validate(call.model_dump())
        request_hash = stable_model_hash(call)
        with self.database.transaction(write=True) as connection:
            saved = self.read(connection, call)
            if saved is not None:
                if saved.status == "pending":
                    raise ToolExecutionPending(
                        "tool execution is in flight or ambiguous; remote reconciliation required"
                    )
                return saved
            connection.execute(
                "INSERT INTO tool_executions VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL)",
                (
                    *identity(call),
                    call.model_dump_json(),
                    request_hash,
                    stable_value_hash(call.arguments),
                ),
            )
        # The claim is committed before any remote effect. Exceptions (including result-save
        # failure) leave it pending. A second process cannot acquire the same identity.
        result = tool.execute(
            call.model_copy(deep=True), idempotency_key=f"saga-tool-{request_hash}"
        )
        result = TypeAdapter(JsonValue).validate_python(result)
        payload = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.database.transaction(write=True) as connection:
            changed = connection.execute(
                """UPDATE tool_executions SET status = 'completed', result_json = ?
                   WHERE world_id = ? AND branch_id = ? AND event_id = ? AND tool_call_key = ?
                   AND status = 'pending' AND request_hash = ?""",
                (payload, *identity(call), request_hash),
            ).rowcount
            if changed != 1:
                raise ToolIdentityConflict("tool claim changed before result persistence")
            saved = self.read(connection, call)
            assert saved is not None
        return saved
