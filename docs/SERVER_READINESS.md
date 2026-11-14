# FinPilot — Deployment summary

FinPilot runs in production at [finpilot.hishamjouda.com](https://finpilot.hishamjouda.com) on a single self-hosted server. This page is the public summary. The full server readiness report, with its inspection details and inventory of the machine, is kept privately with the owner.

## Where it runs

- One home server running Ubuntu Server and Docker, which also hosts the owner's other projects.
- FinPilot is its own Docker Compose project: its own containers, networks, volumes, and PostgreSQL instance. It never connects to another application's database, and it doesn't share a reverse proxy with other sites.
- Every container has fixed memory, CPU, and process limits, sized so FinPilot can't starve the other services on the machine.

## How requests reach it

- Visitors connect over HTTPS through Cloudflare Tunnel, which connects out from the server, so no port is open on the home network.
- The tunnel forwards to FinPilot's own Caddy proxy on a loopback-only port. Caddy routes to the web app and the API, and trusts the client address only from the tunnel's own network.
- The API runs on a database role that owns nothing, with row-level security on every personal table. Trip members can read their trip's shared records; personal accounts and transactions stay with their owner.

## How releases get there

- CI runs on GitHub-hosted runners for every push to `main`. After it passes, the Release workflow runs the complete production stack in containers ([`deploy/scripts/stack-test.sh`](../deploy/scripts/stack-test.sh)) and only then publishes the images to GitHub Container Registry.
- The server pulls releases itself on a timer. It accepts only images pinned by digest, backs up the database before every deployment, and holds any migration that could delete data until the owner approves it. GitHub has no access to the server.
- [`deploy/README.md`](../deploy/README.md) and [`deploy/server/README.md`](../deploy/server/README.md) describe packaging, the server setup, and rollback.

## Backups

- A daily, encrypted [restic](https://restic.net/) backup of the database to a disk separate from the system disk, checked after each run.
- Not yet in place: an off-site copy. It's listed as an open launch risk in [`deploy/server/README.md`](../deploy/server/README.md).

## Open items

- An off-site backup copy.
- Running the container stack test on the server itself, in addition to GitHub's runners, to cover the server's own kernel and sandbox settings.
- The launch items in [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md): a legal review of the privacy notice, account erasure in production, and a daily cap on new accounts.
