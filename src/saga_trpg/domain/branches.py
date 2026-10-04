from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch_id: str = Field(min_length=1)
    source_branch_id: str = Field(default="main", min_length=1)
    # Required and nullable: null deliberately selects the initial world, not the head.
    at_event_id: str | None = Field(min_length=1)


class BranchInfo(BaseModel):
    schema_version: Literal[1] = 1
    world_id: str
    branch_id: str
    parent_branch_id: str | None = None
    fork_event_id: str | None = None
    fork_seq: int = Field(default=0, ge=0)
