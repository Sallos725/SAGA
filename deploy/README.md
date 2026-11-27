# Isolated PocketRisu playtest deployment

This directory contains Docker Compose files for running SAGA alongside PocketRisu without
touching an existing installation:

- `compose.risu-6011.yaml`: starts both a dedicated `saga-pocketrisu` host (on port `6011`) and
  the `saga-runtime` backend (`127.0.0.1:4851` on the host, `http://saga-runtime:4850` inside the
  Docker network).
- `compose.risu-6131.yaml`: starts only a backend (`saga-runtime-6131`, data volume
  `saga-runtime-6131-data`, debug API on `127.0.0.1:4852`) for an existing host-network PocketRisu.
  Point that host's plugin `server_url` at `http://127.0.0.1:4852`; PocketRisu's local-network
  route accepts loopback.

The pinned PocketRisu image is
`ghcr.io/pocketrisu/pocketrisu@sha256:409a5243295f54f1f99a552dc04659fa2ac69bd7c1ab6ee99132b5744ed893bd`.
The SAGA runtime is built from this checkout using the root `Dockerfile`.

## Start the isolated stack

For a fresh installation, create the external Docker resources first, then launch the stack from
the repository root (skip resource creation if they already exist):

```bash
docker network create saga-playtest
docker volume create saga-pocketrisu-save
docker volume create saga-pocketrisu-backups
docker compose -f deploy/compose.risu-6011.yaml config --quiet
docker compose -f deploy/compose.risu-6011.yaml up -d --build
```

By default, PocketRisu binds only to `127.0.0.1:6011`. To expose it on a LAN or private VPN
interface (such as Tailscale), set `POCKETRISU_BIND_HOST` before running Compose:

```bash
POCKETRISU_BIND_HOST=<your-lan-or-vpn-ip> docker compose -f deploy/compose.risu-6011.yaml up -d --build
```

PocketRisu's first-run password setup follows the host's normal UI flow; no credentials are stored
in the repository. After starting the stack, follow the
[starter plugin and card guide](../packs/risu-starter/README.md). Set `useSayNothing=false` in
PocketRisu host settings so an empty Continue action is rejected cleanly before reaching SAGA.

If `saga-pocketrisu` was already started with `docker run`, leave it running and start only the
backend with `docker compose -f deploy/compose.risu-6011.yaml up -d --build saga-runtime`. Compose
cannot adopt an existing container with the same name; do not remove save volumes to resolve a
container-name conflict.

## Smoke checks

To exercise the plugin end-to-end without the browser UI (install hooks, module attachment,
commands, footer, unload), run:

```bash
node scripts/smoke_plugin.cjs --base http://127.0.0.1:4851 \
  --module packs/rulesets/fate-condensed/risu-module.json "overcome investigate 2"
```

Basic health and container checks:

```bash
curl --fail http://127.0.0.1:4851/health
docker compose -f deploy/compose.risu-6011.yaml ps
docker logs saga-runtime --tail 50
```

## Security and optional services

- **Local-only debug API**: `saga-runtime` exposes the debug HTTP API only on `127.0.0.1:4851`
  (or `127.0.0.1:4852` for the backend-only file). PocketRisu reaches `http://saga-runtime:4850`
  over the private Docker network. Debug routes do not yet have production authentication; do not
  publish port `4850` on public interfaces.
- **OpenAI-compatible LLM**: To use server-side OpenAI-compatible intent/narration (OpenRouter,
  Ollama, etc.), configure `SAGA_LLM_BASE_URL`, `SAGA_LLM_API_KEY`, `SAGA_NARRATOR_MODEL`, and
  optionally `SAGA_INTENT_MODEL` in your environment (see [`.env.example`](../.env.example)).
- **NMOS semantic memory**: Disabled unless `SAGA_NMOS_URL` points to an NMOS sidecar
  ([docs/NMOS_SIDECAR.md](../docs/NMOS_SIDECAR.md)); neither compose file enables it by default.

## Persistence and backups

- `saga-pocketrisu-save`: host chats, plugin configuration, and stable host identity.
- `saga-pocketrisu-backups`: host backups.
- `saga-runtime-data`: authoritative `worlds.sqlite3`, journal, tool tape, and fork provenance.

For a consistent backup, stop **both dedicated containers**, archive these three named volumes to
operator-owned storage, then restart the same containers. Keep the runtime seed configuration and
the plugin's `host_instance_id` unchanged when moving or restoring. Do not run
`docker compose down -v` for routine maintenance; restoring only a card or chat backup cannot
restore the runtime journal. See [docs/PERSISTENCE.md](../docs/PERSISTENCE.md) for storage v2
details and explicit v1 migration steps.
