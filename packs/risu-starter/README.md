# RisuAI Starter Pack Placeholder

This directory is the future user-facing "통팩" surface.

Do not invent a stale RisuAI card schema here. When implementing the pack, inspect the current RisuAI export/card/plugin formats and generate valid artifacts from a source manifest.

Planned contents:

```text
packs/risu-starter/
  card/               exported character/card artifact(s)
  lorebook/           optional world/rules lore
  config/             runtime/campaign defaults
  pack-manifest.yaml  source-of-truth pack metadata
  README.md           install/use instructions
```

The backend remains authoritative. Card status text must not become the canonical state store.
