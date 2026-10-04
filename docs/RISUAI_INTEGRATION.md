# RisuAI Integration Notes

## Target UX

Ideal user path:

1. Start backend (`docker compose up -d`).
2. Point RisuAI Custom API at the backend.
3. Import/use an existing or bundled card.
4. Play normally.
5. Runtime tracks exact game state; NMOS supplies long-horizon recall.
6. Regenerate prose without double-applying game effects.

## Adapter responsibilities

The RisuAI adapter is responsible for translating chat protocol semantics into runtime semantics.

It must determine:

- world/campaign id
- player/actor id
- latest user action
- whether this request is a new action or narration regeneration
- which committed event should be narrated

It must not:

- calculate combat rules in HTTP route code
- update HP from model-generated status windows
- re-roll because the user regenerated text
- expose hidden actor memories to the wrong narrator

## Host identity reference: oneuldo memory pilot

Reviewed on 2026-10-04: `Sallos725/oneuldo`, branch `codex/memory-pilot-6010`,
commit [`b6eb2ff90b6cff2110f77925fde2c5504b4c0086`](https://github.com/Sallos725/oneuldo/tree/b6eb2ff90b6cff2110f77925fde2c5504b4c0086).
This provides a concrete plugin-assisted route to turn identity even when the
OpenAI-compatible request itself lacks host ids.

The [plugin](https://github.com/Sallos725/oneuldo/blob/b6eb2ff90b6cff2110f77925fde2c5504b4c0086/card/plugins/oneuldo_memory_pilot.js)
reads the selected chat with `getChatFromIndex`. Its snapshot preserves `chat.id`
as the conversation id and each `message.chatId` as that message's id, together
with current text, role, disabled status, and comment status. It rejects missing ids.
`beforeRequest` synchronizes before the main model request, and the `output` hook
synchronizes after generation. It checks for chat/swipe changes during asynchronous
work and avoids injecting a result for a changed snapshot.

The pilot's server uses a full-snapshot SHA-256 revision and `expectedVersion` for
idempotent synchronization and conflicting-write rejection. The injected model packet
contains chat id, revision, and filtered knowledge; it does not already provide a
SAGA action id or dispatch SAGA events.

### Applying this to SAGA

- Resolve the host instance and chat id to a persistent SAGA world/branch mapping.
  Identify an action by its user-message id within the mapped history, retaining
  input content/version and the authoritative parent event to detect edits.
- Reuse the same committed event for an unchanged user action on regeneration or
  retry. A new message id is a new action even if its text repeats. An edited action
  or changed authoritative prefix needs a branch from the matching committed prefix.
- Do not use the pilot's whole-snapshot revision as the action id: it includes
  assistant text and therefore changes during narration regeneration. Its revision
  contract is useful for synchronization, separately from action idempotency.
- Use the host hooks to send explicit turn metadata to outer orchestration. Keep
  game-event validation and rule execution in SAGA. The pilot rebuilds NPC knowledge
  from selected assistant tags; SAGA cannot use regenerated prose to rewrite an
  already committed roll, damage result, or rule-relevant knowledge event.
- Retain semantic memory through `MemoryPort`; importing the pilot's separate
  memory store is unnecessary. Its memory failure can omit context, whereas a failed
  SAGA dispatch must not be presented as a successfully resolved game action.

### Evidence and remaining work

At the pinned revision, independently ran `npm run build` followed by
`node --test dist/tests/pilot.test.js dist/tests/pilot-plugin.test.js dist/tests/pilot-remote.test.js`
from `mcp/` under Node 22.23.3: **5 passed, 0 failed**. This covers the shipped plugin
in a VM with a real local HTTP server, revision conflicts, regeneration/swipe/edit/delete
memory projection, and persistence across a new process. It does not run a RisuAI UI.

The upstream [verification report](https://github.com/Sallos725/oneuldo/blob/b6eb2ff90b6cff2110f77925fde2c5504b4c0086/docs/notes/MEMORY_PILOT_VERIFICATION.md)
records PocketRisu 1.13.0 UI tests on an isolated host with a deterministic model.
The current plugin and six checked server files match the recorded source hashes.
The [6010 setup report](https://github.com/Sallos725/oneuldo/blob/b6eb2ff90b6cff2110f77925fde2c5504b4c0086/docs/notes/MEMORY_PLAYTEST_6010.md)
separately records installation and manual synchronization; actual PM-provider model
generation was not part of that installation verification. Neither host was accessed
or modified during this review.

Next, implement the plugin-to-SAGA turn mapping and verify the current target host's
regeneration, continuation, edited-input, repeated-text/new-message, and auxiliary-request
paths. The ten-regeneration acceptance test below must also prove no repeated dice/tool
execution. Host ids are an available integration route, not yet a completed SAGA binding.

## Card compatibility strategy

Prefer a compatibility ladder:

1. Existing card, no modification: runtime acts as transparent orchestration layer where possible.
2. Optional small metadata block: campaign/ruleset/module hints.
3. Full runtime-aware card: explicit stats, IDs, module hooks, status UI.

Do not require every card to be rewritten just to test the engine.

## Status display

Generate status from authoritative state after commit. If injected into model context, label it as read-only structured state. Prefer UI/tool surfaces over asking the model to reprint numbers faithfully.

## Regeneration test

Mandatory acceptance test:

- Submit one attack action.
- Capture event id, roll records, target HP, journal length.
- Request 10 narration regenerations.
- Assert all four values are unchanged.
- Prose may differ.
