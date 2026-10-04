from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RulesetReference(BaseModel):
    ruleset_id: str
    ruleset_version: str


class CreateWorldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ruleset_id: str = Field(min_length=1)
    ruleset_version: str = Field(min_length=1)


class RulesetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    ruleset_id: str
    ruleset_version: str
    title: str
    implementation_scope: Literal["example", "legacy-scaffold"]
    source_url: str | None = None
    source_revision: str | None = None
    license_id: str | None = None
    license_url: str | None = None
    attribution: str = ""
    modifications: str
    implemented: list[str]
    not_implemented: list[str]
    narrator_instructions: str
    event_payload_schemas: dict[str, dict[str, Any]] = Field(default_factory=dict)


class RulesetContext(BaseModel):
    common_bot_description: str
    manifest: RulesetManifest
