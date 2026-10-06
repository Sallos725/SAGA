"""Model drivers: turn a public observation into one command line.

`PromptDriver` renders the observation with a versioned template and hands the text to any
async completion function; it then takes the last non-empty line of the answer as the
command (code fences and backticks stripped). `END` stops the run early. Whether the line is
legal is for the runtime to decide; the driver never sees validators.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from saga_trpg.benchmark.rulecheese import Observation

if TYPE_CHECKING:  # provider extras are optional; only VertexDriver needs them
    import httpx
    from pydantic import SecretStr

PROMPT_TEMPLATE_VERSION = "rulecheese-prompt-v1"
STOP_WORD = "END"
_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*$")

SYSTEM_INSTRUCTION = (
    "You are playing a tabletop scenario through a rules engine. Each turn, answer with "
    "exactly one command line from the command list, nothing else: no prose, no "
    "explanation, no quotes around the whole line. Values fill the inputs in order; "
    "name=value sets one by name; quote text that contains spaces. Write END on its own "
    "line to stop early. Illegal commands are rejected and cost an attempt; the error you "
    "see is accurate. Only the state shown is true; do not assume anything else."
)


def render_prompt(observation: Observation) -> str:
    lines = [
        f"Scenario: {observation.scenario_id}",
        "",
        observation.setup_text.strip(),
        "",
        f"Turn {observation.turn} of {observation.turn + observation.turns_left - 1}; "
        f"{observation.attempts_left} attempt(s) left.",
        "",
        "Current state:",
        json.dumps(observation.display, ensure_ascii=False),
        "",
    ]
    if observation.last_command is not None:
        lines.append(f"Your last command: {observation.last_command}")
        if observation.last_error:
            lines.append(f"It was rejected: {observation.last_error}")
        elif observation.last_facts:
            lines.append("Result: " + " ".join(observation.last_facts))
        lines.append("")
    lines += ["Commands:", observation.commands, "", "Your command:"]
    return "\n".join(lines)


def parse_answer(text: str) -> str | None:
    """The last meaningful line of a model answer, or None for the stop word."""
    candidates = [
        line.strip().strip("`").strip()
        for line in text.splitlines()
        if line.strip() and not _FENCE.match(line.strip())
    ]
    if not candidates:
        return ""
    command = candidates[-1]
    if command.upper() == STOP_WORD:
        return None
    return command


class PromptDriver:
    def __init__(
        self, complete: Callable[[str], Awaitable[str]], *, name: str = "prompt-driver"
    ) -> None:
        self._complete = complete
        self.name = name
        self.calls = 0
        self.stopped_early = False

    async def decide(self, observation: Observation) -> str | None:
        self.calls += 1
        answer = await self._complete(render_prompt(observation))
        command = parse_answer(answer)
        if command is None:
            self.stopped_early = True
        return command

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "template": PROMPT_TEMPLATE_VERSION,
            "calls": self.calls,
            "stopped_early": self.stopped_early,
        }


class VertexDriver(PromptDriver):
    """Gemini through the same bounded Vertex path the narrator uses (one JSON credential)."""

    def __init__(
        self, account_json: SecretStr, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        from saga_trpg.adapters.narrator.vertex import MODEL, VertexNarrator

        self._narrator = VertexNarrator(account_json, transport=transport)
        super().__init__(self._generate, name=f"vertex:{MODEL}")

    async def _generate(self, prompt: str) -> str:
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "thinkingConfig": {"thinkingLevel": "LOW", "includeThoughts": False},
                "maxOutputTokens": 256,
            },
        }
        try:
            return await self._narrator.generate(body)
        except Exception as exc:
            # Never retry (each call is billed) and never echo provider payloads or tokens.
            raise ValueError(f"Vertex call failed: {type(exc).__name__}") from None
