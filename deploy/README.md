# Isolated PocketRisu 6011 playtest

A second file, `compose.risu-6131.yaml`, starts only a backend (`saga-runtime-6131`, data
volume `saga-runtime-6131-data`, debug API on `127.0.0.1:4852`) for an existing PocketRisu
that runs with host networking on port 6131. Point that host's plugin `server_url` at
`http://127.0.0.1:4852`; PocketRisu's local-network route accepts loopback. Nothing in it
touches the 6011 containers or volumes.

The compose file reproduces the dedicated `saga-pocketrisu` host and `saga-runtime` backend.
It does not modify the existing PocketRisu host on 6010. The pinned PocketRisu image is
`ghcr.io/pocketrisu/pocketrisu@sha256:409a5243295f54f1f99a552dc04659fa2ac69bd7c1ab6ee99132b5744ed893bd`.
The runtime is built from this checkout using the existing Dockerfile.

For a fresh installation, create the external resources first:

```bash
docker network create saga-playtest
docker volume create saga-pocketrisu-save
docker volume create saga-pocketrisu-backups
docker compose -f deploy/compose.risu-6011.yaml config --quiet
docker compose -f deploy/compose.risu-6011.yaml up -d --build
```

Run these from the repository root; skip resource creation if they already exist. The file
binds PocketRisu to this server's localhost, Tailscale (`100.96.204.101`) and LAN
(`192.168.219.103`) addresses on port 6011. Adjust those host addresses on another machine.
PocketRisu's first-run password setup remains the host's normal UI flow; no credential is
stored in the repository. Import the [starter card and plugin](../packs/risu-starter/README.md).
Set `useSayNothing=false` in this dedicated host's settings. The default true setting converts
an empty Continue click into an unsupported `*says nothing*` user command; SAGA rejects it
without mutation, but disabling insertion is required for rejection before any backend request.

If `saga-pocketrisu` was already started with `docker run`, leave it running and start only
the backend with `up -d --build saga-runtime`. Compose cannot adopt an existing container with
the same name; replacing that container is a separate explicit operator action, using the
same named volumes. Do not remove save volumes to solve a container-name conflict.

To exercise the plugin path end to end without the UI (module → attachment → commands →
footer), run `node scripts/smoke_plugin.cjs --base http://127.0.0.1:4852 --module
packs/rulesets/fate-condensed/risu-module.json "overcome investigate 2"` against a backend.

```bash
curl --fail http://127.0.0.1:4851/health
docker compose -f deploy/compose.risu-6011.yaml ps
docker logs saga-runtime --tail 50
```

The backend exposes the debug API only on `127.0.0.1:4851`; PocketRisu reaches
`http://saga-runtime:4850` through the dedicated Docker network. Existing debug routes have
no production authentication. Keep this network limited to trusted containers and do not
publish port 4850 on public interfaces.

Persistence:

- `saga-pocketrisu-save`: host chats, plugin configuration and stable host identity.
- `saga-pocketrisu-backups`: host backups.
- `saga-runtime-data`: authoritative `worlds.sqlite3`, journal and fork provenance.

For a consistent backup, stop **both dedicated containers**, archive these three named
volumes to operator-owned backup storage, then start the same containers. Retain the runtime
seed configuration and the same plugin host identity. Do not use `docker compose down -v`
for routine maintenance. Restoring only a card/chat cannot restore the runtime journal.
Storage v2 is unchanged by this integration; see [persistence](../docs/PERSISTENCE.md) for
the existing explicit v1 migration procedure.
