# Packaging the eventual TRPG "통팩"

Keep source and generated artifacts separate.

## Developer repository

Contains runtime, adapters, rulesets, tests, benchmark scenarios, and source manifests.

## User-facing bundle

Suggested release shape:

```text
nmos-trpg-pack-vX.Y.Z/
  README.md
  docker-compose.yml
  .env.example
  risu/
    <current valid card export>
    <current valid lorebook/module exports>
  campaigns/
    starter/
  rulesets/
    generic-fantasy/
```

Prefer one startup path:

```bash
cp .env.example .env
docker compose up -d
```

Then the user should only need to import the RisuAI artifact and set the Custom API endpoint.

## Release checks

- pack format validated against current RisuAI version
- no secrets in exports
- backend health check documented
- migration path documented
- reroll invariants tested end-to-end
- NMOS unavailable behavior tested
- save/export/backup instructions included
- licensing for bundled rules/content verified
