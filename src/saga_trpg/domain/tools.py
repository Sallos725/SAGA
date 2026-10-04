from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ToolCall(BaseModel):
    """A logical invocation. Context binds a result to its rule/event/state inputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    world_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    tool_call_key: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    tool_version: str = Field(min_length=1)
    protocol_version: str = Field(min_length=1)
    arguments: dict[str, JsonValue]
    context_hash: str = ""


class ToolExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    call: ToolCall
    arguments_hash: str
    status: Literal["pending", "completed", "committed"]
    result: JsonValue = None
