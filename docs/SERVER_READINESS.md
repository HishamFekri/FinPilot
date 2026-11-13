# FinPilot — Home Server Readiness (P3)

| | |
|---|---|
| Inspection | 2026-11-12 20:49 UTC (23:49 server time, +03). The owner ran [`deploy/inspection/inspect-server.sh`](../deploy/inspection/inspect-server.sh) over SSH; the agent never connected. |
| Server | The home server: Lenovo ThinkPad X390 (20Q1S3JV21), Ubuntu 24.04.5 LTS |
| Capacity verdict | **Capable.** Ample spare CPU, memory and disk for FinPilot's limits. |
| Readiness verdict | **Not ready for public launch.** Blockers are listed in §16. |
| Isolated Docker integration test | **GO, with conditions** (§20); P4 prepared it, results pending (§21) |

**How to read this:**
- **Verified** means it appears directly in the inspection output.
- **Inferred** means a conclusion drawn from verified facts, with the reasoning given.
- **Estimate** means a planning figure that wasn't measured.
- **Unknown** means it couldn't be read without root, or wasn't collected.

Nothing on the server was changed. The script only reads, and it ran without `sudo`.

---

## 1. Executive summary

- **Capacity (verified):**
  - 4 cores and 8 threads, a load average of 0.10, and CPU 97–98 % idle;
  - **14.3 GB available memory** out of 16 GB, with swap unused;
  - **72 GB free** on the root disk, where Docker keeps its data;
  - **1.8 TB free** on `/srv/storage`.

  FinPilot's P2a caps of 2.5 GiB and 4 CPUs fit with wide margins.
- **Public traffic (verified, partly inferred):**
  - Nothing on the host listens on ports 80 or 443.
  - `cloudflared` runs as a host service, and the existing **Caddy container is published only on `127.0.0.1:8080`**.
  - Public traffic therefore arrives through a **Cloudflare Tunnel into the existing Caddy** (inferred), with no router port forwards in this path.
- **Conflicts (verified):** FinPilot's default HTTP port `127.0.0.1:8080` collides with the existing Caddy. FinPilot's networks (`172.30.10.0/24`, and `172.30.250.0/24` for the stack test) and the stack-test ports (18443 and 18080) are free.
- **New findings that change the plan:**
  1. **The existing Caddy doesn't pass on real client addresses** (verified: no `trusted_proxies` or `client_ip_headers` in its config). Behind it, every FinPilot visitor would look like one address, so the per-visitor sign-in limits would become one shared limit. This is a public-launch blocker, not a test blocker.
  2. **FinPilot's container user IDs collide with host users** (verified):
     - web uid 1000 is the administrator account, which is in the `sudo` and `docker` groups;
     - db uid 999 is `telegrambot`;
     - api uid 10001 is already used by the AI Document Assistant's containers.
  3. **Docker requires `/srv/storage`** (verified: `Requires=srv-storage.mount`). If the external SSD fails or disconnects, Docker itself stops, and every app goes down with it, FinPilot included.
  4. **`age` isn't installed** (verified), but `restic` and `gpg` are. P2a's encrypted backup path needs `age`, so it needs either an install or a change of tool.
- **Port 9090** listens on all interfaces. It isn't a Docker port (inferred, §9). The most likely source is a systemd socket-activated service such as Cockpit, but that isn't identified. It isn't proven to be reachable from outside.

## 2. Inspection date

2026-11-12 20:49:41 UTC. The server had been up 4 days and 3 h, since 2026-11-08 20:03 local time.

## 3. SSH connection status

- **Agent:** batch SSH couldn't sign with the passphrase-protected key, and the Windows `ssh-agent` is disabled. The agent didn't connect this phase, as instructed.
- **Owner:** ran the script and saved the output to `Documents\finpilot-server-inspection.txt`.
- **Server SSH (verified):**
  - `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PubkeyAuthentication yes` and `PermitRootLogin no`;
  - `50-cloud-init.conf` wasn't readable, but the effective setting is still verified, because the server offered **only** `publickey` during the earlier connection attempt;
  - one authorised key;
  - it listens on `0.0.0.0:22` and `[::]:22`; whether the LAN can reach it depends on UFW rules, which weren't readable (§13).

## 4. Verified hardware specifications

| Item | Value |
|---|---|
| Model | Lenovo ThinkPad X390, 20Q1S3JV21; BIOS N2JETA3W (1.81), 2023-11-20 |
| CPU | Intel Core i5-8365U, 4 cores and 8 threads, 1.6–4.1 GHz, VT-x |
| Memory | 15.3 GiB total (16,019,948 kB); 14.3 GB available |
| Swap | 4 GiB file `/swap.img`, 0 used; swappiness 60 |
| Internal disk | 256 GB NVMe (HS-SSD-WAVE(P)), LVM; the root logical volume is **100 GB of 235 GB**, so about 135 GB of the volume group is unallocated (inferred from the sizes) |
| External disk | 1.9 TB `sda`, USB 3 (UAS, 5 Gbps), ext4, mounted at `/srv/storage` |
| Kernel | 6.8.0-147-generic, x86_64 |
| Network | USB Ethernet adapter on the LAN (default route, metric 100); Wi-Fi on the same LAN (metric 600); onboard Ethernet down; Tailscale |

## 5. System health

| Measure | Value (verified) | Against the §5 criteria |
|---|---|---|
| Load average | 0.10 / 0.10 / 0.18 | Proceed |
| CPU | 97–98 % idle; 1 % user and 1 % system | Proceed |
| CPU pressure `some avg300` | 0.00 | Proceed |
| Memory pressure `some avg300` | 0.00 | Proceed |
| I/O pressure `some avg300` | **2.19** | Proceed (under 5). It's constant background I/O, most likely Netdata, which has read 35.5 GB and written 2.6 GB since boot, and Uptime Kuma, which has written 4.6 GB (inferred). |
| OOM kills this boot | none | Proceed |
| Temperatures | package 36–45 °C, NVMe 31 °C, fan 0 RPM | Proceed |
| Committed memory | 5.3 GB of a 12.2 GB commit limit | Proceed |

One snapshot isn't a guarantee of capacity. The AI Document Assistant's peak load wasn't observed.

## 6. Docker and existing container inventory

**Engine (verified):**
- Docker 29.9.2 (API 1.56) and Compose v5.6.1;
- the `overlayfs` snapshotter, the systemd cgroup driver and **cgroup v2**, so limits are enforced;
- security options: AppArmor `default`, seccomp `builtin` and cgroupns;
- `live-restore` is off, and the root directory is `/var/lib/docker`, on the internal NVMe;
- 19 images and 11 containers, 9 of them running.

There's no `daemon.json`, or it isn't readable.

**Your access:** you're in the `docker` group (gid 988), so the stack test can run without `sudo`.

| Container | Compose project | Image | State | Restart | Published |
|---|---|---|---|---|---|
| ai-frontend | ai-document-assistant | local build | Up 4 d, healthy | **no** | none (3000 internal) |
| ai-backend | ai-document-assistant | local build | Up 4 d, healthy | **no** | none (8000 internal) |
| hesham-postgres | databases | pgvector/pgvector:0.8.1-pg17 | Up 4 d, healthy | **no** | none |
| hesham-redis | databases | redis:7.4-alpine | Up 4 d, healthy | **no** | none |
| caddy | caddy | `caddy:2` (floating tag) | Up 4 d, no healthcheck | unknown* | `127.0.0.1:8080→80` |
| hesham-portfolio | — (not Compose) | nginx:stable-alpine | Up 4 d | unknown* | `SERVER_TAILSCALE_IP:8081→80` |
| netdata | netdata (Portainer stack) | netdata/netdata:stable | Up 4 d, healthy | unless-stopped | `SERVER_TAILSCALE_IP:19999` |
| uptime-kuma | monitoring (Portainer stack) | louislam/uptime-kuma:2 | Up 4 d, healthy | unless-stopped | `SERVER_TAILSCALE_IP:3001` |
| portainer | — | portainer-ce:lts | Up 4 d | unknown* | `SERVER_TAILSCALE_IP:9443` |
| hesham-portfolio-backup | — | hesham-portfolio:latest | Exited (0), 2 weeks ago | — | — |
| portainer-old | — | portainer-ce:lts | Exited (2), 2 weeks ago | — | — |

\* The script's settings template stopped on containers without a healthcheck (`map has no entry for key "Health"`). The script is now fixed (§19).

**Settings found (verified):**
- No container has memory, CPU or process limits (`mem=0 cpus=0`).
- The AI containers run as `10001:10001` with `no-new-privileges`.
- The databases run as the image default, with no extra options.
- **Netdata** runs with `pid=host`, `CAP_SYS_ADMIN` and `CAP_SYS_PTRACE`, and with AppArmor unconfined.
- **Portainer** mounts `/var/run/docker.sock` read-write.

**Networks (verified):**
- `bridge` 172.17/16; `monitoring_default` 172.18/16; `netdata_default` 172.19/16;
- `proxy` 172.20/16, which carries caddy and ai-frontend;
- `hesham-databases` 172.21/16, which is **internal** and carries the databases and ai-backend.

**Volumes (verified):** 7 named volumes (caddy, uptime-kuma, netdata ×3, portainer), using 1.56 GB. The databases and uploads are bind mounts on `/srv/storage` (§8). Images take 6.5 GB and the build cache 3.7 GB.

**Separate Compose project (verified):** there are no `finpilot*` containers, networks or volumes, so `finpilot` and `finpilot-stacktest` are free as project names.

## 7. Current resource consumption

From one `docker stats` sample (verified):

| Container | CPU | Memory |
|---|---|---|
| **netdata** | 7.0 % | **563 MiB** |
| uptime-kuma | 0.9 % | 234 MiB |
| ai-backend | 0.3 % | 213 MiB |
| ai-frontend | 0.0 % | 118 MiB |
| hesham-postgres | 1.0 % | 98 MiB |
| portainer | 0.0 % | 92 MiB |
| caddy | 0.0 % | 54 MiB |
| hesham-portfolio | 0.0 % | 15 MiB |
| hesham-redis | 1.2 % | 14 MiB |
| **Total** | **≈ 10 %** of one thread | **≈ 1.4 GiB** |

On the host, the largest processes are:
- the GitHub Actions runner (`Runner.Listener`, 136 MB, running as the `portfolio-runner` user);
- dockerd (115 MB) and containerd (70 MB);
- fwupd, tailscaled, cloudflared and the Telegram bot (`telegrambot`, 44 MB).

The AI Document Assistant is idle in this sample. Its peak is **unknown**, and it has no memory limit.

## 8. Storage and mount assessment

**Verified:**
- `/dev/sda1`, ext4, 1.9 TB with 596 MB used, mounted at `/srv/storage`.
- It's in fstab **by UUID**, with `nofail,x-systemd.device-timeout=10s`, and `mountpoint` confirms it's mounted.
- It's on USB 3 at 5 Gbps using UAS. The USB Ethernet adapter, which carries the default route, is on **the same USB root hub** (bus 2).
- Mount unit `srv-storage.mount` is active. These depend on it (`RequiredBy`): `docker.service`, `docker-projects.service`, `hesham-databases.service`, `ai-document-assistant(-deploy).service`, `server-backup.service` and `tailscale-docker-start.service`. Docker also has `RequiresMountsFor=/srv/storage`.
- Top-level folders: `docker-data`, `backups`, `projects`, `uploads`, `sites`, `databases`.
- Bind mounts on the SSD: the Postgres data (`/srv/storage/databases/postgres`), Redis, the AI uploads and the portfolio site.
- No disk I/O errors, USB resets or ext4 errors this boot or the previous one (only normal UAS start-up lines).
- `smartmontools.service` is running.

**Unknown:**
- SMART health: `smartctl` needs root;
- filesystem check history: `tune2fs` needs root.

**If the SSD disconnects (inferred from `Requires=`):**
- systemd stops `docker.service`, so **every container stops**, FinPilot included, even with FinPilot's data on the NVMe.
- At boot without the SSD, Docker doesn't start, so no app can silently write to an empty mount point. That's protective for the existing bind mounts, but it puts the SSD on every app's critical path.
- Losing the shared USB hub could drop both the storage and the wired network. Wi-Fi is a verified backup route.

### FinPilot storage layout (proposed, nothing created)

| What | Where | Why |
|---|---|---|
| Live database | Named volume `finpilot_db_data` under `/var/lib/docker` (NVMe) | Not on the USB device; 72 GB free |
| Caddy data | Named volume `finpilot_caddy_data` (NVMe) | Same reason |
| Deployment folder | `/srv/docker/finpilot/` (matches the existing `/srv/docker/<app>/` convention; that folder is on the NVMe) | Consistent with the other apps |
| Secrets | `/srv/docker/finpilot/secrets/`, root-owned, per-file group | P2a `init-secrets.sh` |
| Local encrypted backups | `/srv/storage/backups/finpilot/`, mode 0700 | Different device from the live data |
| Off-site copy | Object storage (§12) | Survives losing the laptop |

Keeping FinPilot's database on the NVMe means losing the SSD can't take the live data with it. It still can't keep FinPilot running, because Docker depends on the SSD. Decoupling Docker from the mount is a systemd change for the owner to decide. It isn't recommended now, because the existing apps rely on the dependency.

## 9. Network and port inventory

| Listener (verified) | Bound to | Owner | Evidence |
|---|---|---|---|
| 22/tcp | all interfaces (IPv4 and IPv6) | sshd | key-only (§3) |
| 8080/tcp | 127.0.0.1 | **existing Caddy** (Docker) | `docker ps` |
| 8081/tcp | Tailscale IP | portfolio nginx | `docker ps` |
| 3001/tcp | Tailscale IP | Uptime Kuma | `docker ps` |
| 19999/tcp | Tailscale IP | Netdata | `docker ps` |
| 9443/tcp | Tailscale IP | Portainer | `docker ps` |
| **9090/tcp** | **all interfaces, dual-stack (`*`)** | **unidentified** | see below |
| 20241/tcp | 127.0.0.1 | cloudflared metrics (inferred: its default range starts at 20241) | — |
| 52663, 59561/tcp | Tailscale addresses only | tailscaled peer API (inferred) | — |
| 41641/udp | all | Tailscale WireGuard | standard port |
| 53 | 127.0.0.53, 127.0.0.54 | systemd-resolved | — |
| several high UDP ports on `*` | all | unknown, probably QUIC or Tailscale client sockets | low concern |

Nothing listens on 80, 443, 5432, 5433 or 6379 on the host (verified). Ports 8443, 18080 and 18443 are free.

**Port 9090 (what the evidence supports):**
- It isn't a Docker-published port: it's absent from every `docker ps` port list, and Docker shows its bindings as `0.0.0.0` and `[::]` entries rather than `*` (inferred).
- A `*` listener is a single dual-stack socket. That's typical of a **systemd socket unit** or a Go program listening on `:9090`.
- Port 9090 is Cockpit's default (`cockpit.socket`, socket-activated, so no `cockpit.service` appears while idle), and Prometheus's default. No Prometheus container or service was found. **The most likely candidate is Cockpit or another socket-activated service; this is not confirmed.**
- **Reachability:**
  - **public internet:** no evidence; there's no router forward in this path, and no Tailscale Funnel;
  - **LAN and tailnet:** depends on UFW rules, which weren't readable; UFW's default inbound policy is DROP (verified);
  - **Cloudflare tunnel:** only if its ingress routes to it, which is unknown.
- **To identify it, without changing anything:** run `systemctl list-sockets --all | grep 9090`, which works without root (now in the script), or `sudo ss -tlnp 'sport = :9090'`.
- **FinPilot impact:** none. FinPilot doesn't use 9090.

**Docker and UFW:** Docker's published ports bypass UFW's input rules. Every existing publication is bound to loopback or the Tailscale address (verified), so this is safe today. FinPilot must keep the same rule: loopback, or no publication at all.

## 10. Reverse-proxy assessment

**Verified:**
- No Caddy, Nginx, Apache, Traefik or HAProxy runs as a host service.
- **Caddy** runs as a container, `caddy:2`, defined by `/srv/docker/caddy/compose.yaml` with the Caddyfile at `/srv/docker/caddy/Caddyfile`. It's published only on `127.0.0.1:8080→80`, on the `proxy` network.
- Its Caddyfile structure:
  - `admin off`;
  - one site, `:80`, with `handle @ai { reverse_proxy ai-frontend:3000 }`;
  - a default `handle { … }` whose content didn't match the extractor, so it's probably `file_server`, `respond` or `redir`;
  - **no `tls`, no `trusted_proxies`, no `client_ip_headers`, no `request_body` and no `servers` block.**
- **The portfolio's nginx** serves a static site on the Tailscale address only.
- **cloudflared** is active as a host service, with no tunnel containers.

**Inferred topology:** Cloudflare edge (TLS) → `cloudflared` on the host → `http://127.0.0.1:8080` → the Caddy container → apps by host matcher.

The tunnel's ingress rules weren't read: they're in cloudflared's config or the Cloudflare dashboard. Whether a new hostname would reach Caddy automatically through a catch-all rule is **unknown**.

**What this means for FinPilot:**
1. TLS is terminated by Cloudflare, so FinPilot's Caddy shouldn't obtain certificates or redirect HTTP to HTTPS. Cloudflare handles both. FinPilot still sends HSTS, and its cookies stay `__Host-`, `Secure`.
2. The existing Caddy sees every request as coming from the Docker gateway of its network (*inferred*: cloudflared on the host reaches a loopback-published port through docker-proxy). With no `trusted_proxies`, Caddy **replaces** `X-Forwarded-For` with that gateway address, so FinPilot would see one client address for everybody.
3. The existing Caddy is on a floating `caddy:2` tag with no healthcheck. FinPilot's own proxy layer stays pinned and tested.

## 11. Database isolation assessment

**Existing (verified):**
- `hesham-postgres`, pgvector 0.8.1 on Postgres 17, using 98 MiB;
- data bind-mounted from `/srv/storage/databases/postgres`;
- on the **internal** `hesham-databases` network, with **no published port**;
- Redis is the same.

FinPilot won't touch either.

**FinPilot (P2a, verified locally):**
- a dedicated `postgres:18.1-trixie` container on its own internal `data` network, with no published port and its own named volume on the NVMe;
- `finpilot_owner`, without superuser, database-creation, role-creation or RLS-bypass rights;
- `finpilot_app` as the runtime role, with RLS applied (57 verify checks);
- a separate `MIGRATION_DATABASE_URL`.

**Fit on this server:**
- no port conflicts (verified);
- memory: an estimated 60–120 MiB idle against a 1 GiB cap, out of 14.3 GB available (verified);
- disk: an estimated 0.45 GB image plus under 1 GB of data in year one, against 72 GB free (verified).

**A dedicated database container is practical.**

**One required change:** db uid 999 is the host user `telegrambot` (verified). The official image initialises its data directory as uid 999, so changing it isn't trivial. Recommendation: keep 999 for db, as an accepted risk, and change the api and web user IDs (§17, S-I2).

## 12. Backup and recovery risks

**Verified:**
- `server-backup.timer` runs daily at 03:00. Its last run was 2026-11-12 03:00, and no unit is in a failed state.
- Folders exist at `/srv/storage/backups` (on the SSD) and `/var/backups` (on the NVMe; this is Ubuntu's own dpkg backup).
- Tools: `restic` and `gpg` are installed; **`age`, `rclone`, `borg`, `pg_dump` and `psql` are not**.

**Unknown:**
- what `server-backup.service` copies, and where to;
- whether it encrypts;
- whether anything leaves the house;
- whether a restore has ever been tested.

The script deliberately didn't read the unit's command line or any backup contents. The owner can check with `systemctl cat server-backup.service`.

**Risks:**
- **Inferred:** the existing database backups and the databases themselves are on **the same SSD**. One device failure loses both, unless `server-backup` copies off-site.
- **Single points of failure:** one laptop, one USB SSD, one shared USB hub for storage and Ethernet, and home power and internet.
- **P2a gap:** encrypted backups pipe through `age`, which isn't installed.

**Recommendation for FinPilot (needs approval):**
1. **Use `restic`, which is already installed, instead of adding `age`.**
   - `backup.sh` streams `pg_dump --format=custom` into `restic backup --stdin`.
   - It sends to two repositories: `/srv/storage/backups/finpilot/restic`, on a different device from FinPilot's live data, and an **off-site** repository such as Backblaze B2 or any S3-compatible store, using a bucket with object lock.
   - restic encrypts on the client.
   - The off-site credentials and the repository password live in root-only secret files. A copy of the repository password is kept **off the server**, in your password manager.
2. **Retention:** `restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune`.
3. **A monthly restore drill**, into `finpilot_restore_test`, with `restore.sh` already supporting this.
4. **A heartbeat:** each successful backup pings a push monitor (§13).

This changes P2a's `backup.sh` and `restore.sh`, so it's code work for the next milestone, not done now.

## 13. Monitoring and power reliability

**Power (verified):**
- AC is online.
- The battery is at 95 %, "Not charging", with **702 cycles** and **76 % of its design capacity** (36.6 of 48.0 Wh).
- The charge thresholds are 0 and 100, meaning none is set.
- The lid switch is ignored in every mode. The sleep targets aren't masked, but nothing triggers them.

**Estimate:** about 4–5 hours on battery at the current 6–8 W idle draw. That covers short power cuts. It does nothing for an internet cut.

**Updates (verified):**
- **A reboot is required:** kernel `linux-image-6.8.0-149` is installed, and 147 is running.
- 10 packages can be upgraded: Docker CE 29.9.2→29.9.3, Compose 5.6.1→5.7.1, Tailscale 1.104.2→1.104.6, and linux-firmware.
- Unattended upgrades are on. No `Automatic-Reboot` line was found, so the default (off) applies (inferred).

**Firmware (verified, none applied):**
- **Intel ME:** 192.64.1551 is installed, and 192.95.2489 is available (urgency High, several CVEs). It can't be applied with the lid closed, needs AC, and needs a reboot.
- **Thunderbolt controller:** NVM 20.00 is installed, and 23.00 is available (High).
- **Secure Boot:** KEK 2011 → 2023, db 2011 → 2023, and dbx 20250507 → 20260402. These are all High urgency and all need a reboot.

**Docker restart behaviour:**
- **Verified:** `live-restore` is off, and the AI containers and both databases are `restart: no`.
- **Inferred:** a Docker package upgrade or daemon restart stops every container. Those four would stay down, unless the systemd units (`docker-projects`, `hesham-databases`, `ai-document-assistant`) re-run, and those probably run only at boot. FinPilot uses `restart: unless-stopped` and would come back.
- Docker CE comes from Docker's repository, which unattended-upgrades usually doesn't cover (inferred), so this happens only on a manual upgrade.

**Monitoring (verified):**
- Uptime Kuma and Netdata, both on the tailnet;
- custom systemd timers: `cpu-temperature-monitor`, `server-battery-monitor`, `storage-monitor`, `server-resources`, `ai-document-assistant-check` and `telegram-data-collector`;
- an interactive Telegram bot.

Alerts probably go to Telegram (inferred from the names). All of it runs **on the same laptop**, so an outage of the whole server can't alert anyone.

**What interruptions mean for FinPilot:**
- **Power cut:** the battery bridges it. After a reboot, Docker starts once `/srv/storage` mounts. FinPilot's API reports ready only when the database is reachable, migrations are current and RLS is verified.
- **Internet cut:** the tunnel drops, so the public site is unreachable. Tailnet access over the LAN still works, and Google sign-in fails until the link returns.
- **SSD loss:** everything stops (§8).

## 14. FinPilot resource budget

| Service | P2a limit | Recommended for this server | Expected idle / peak (estimate) |
|---|---|---|---|
| proxy (Caddy) | 128 MiB, 0.5 CPU | keep | 25–40 / 60 MiB |
| web (Next.js) | 384 MiB, 1.0 CPU | keep | 120–180 / 250 MiB |
| api (FastAPI, with Chromium) | 1 GiB, 1.5 CPU, 256 MiB shm | keep the memory; **1.0 CPU** for the beta | 150–220 / 500–700 MiB during a PDF |
| db (Postgres 18) | 1 GiB, 1.0 CPU | **768 MiB**, 1.0 CPU | 60–120 / 300 MiB |
| jobs (migrate, backup) | api and db images | keep | about 150 MiB for under a minute |
| **Total** | 2.5 GiB, 4.0 CPU | **≈ 2.25 GiB, 3.5 CPU** | **≈ 0.4–0.6 / 1.4 GiB** |

**Headroom:**
- 14.3 GB available − 1.4 GiB FinPilot peak ≈ **12.8 GB** left for the existing apps and the page cache.
- The existing containers use about 1.4 GiB today and have no limits.

**Disk (estimate):**
- about 2.5–3 GB of images, under 1 GB of data in year one, and capped logs;
- the stack test also needs about 3–5 GB of build cache.

That's against 72 GB free on the NVMe and 1.8 TB on the SSD (verified).

**PDF rendering:**
- Each export is a burst of about 300–500 MB and up to one thread for 1–3 seconds.
- With one worker, PDFs render one at a time.
- The server has 8 threads, and CPU pressure is 0. **No meaningful competition with the existing apps is expected**, but this is an estimate until the stack test measures it.

**Measure in the test:** `docker stats` during PDF exports, then adjust the limits.

## 15. Deployment architecture recommendation

**Recommended: the existing Caddy stays the single external routing layer. FinPilot keeps its own pinned Caddy behind it, in a new "behind-proxy" mode.**

```
Cloudflare edge (TLS, HSTS preload optional)
  → cloudflared (host service)
    → 127.0.0.1:8080 → existing Caddy container
        @finpilot host matcher → reverse_proxy finpilot-proxy:8080   over network finpilot_edge
          → FinPilot Caddy (no published port, plain HTTP)
              /api/v1/* → api        everything else → web        (front, internal)
              api → db                                              (data, internal)
```

- **Why this design:**
  - It follows the evidence: the tunnel already lands on the existing Caddy.
  - It keeps one place where host names are routed.
  - FinPilot's body limit, `/api/v1` routing, header stripping and log filtering stay in FinPilot's tested Caddyfile.
  - FinPilot publishes **no host port at all**, which also removes the 8080 conflict.
- **The `finpilot_edge` network:**
  - FinPilot's Compose creates it: internal, `172.30.13.0/29` (built in P4), with FinPilot's proxy at `.2` and the existing Caddy at `.3`.
  - The only existing container that joins it is Caddy.
  - It's not the shared `proxy` network: joining that one would leave FinPilot's proxy trusting a 172.20/16 range that also holds ai-frontend.
- **Trust chain (each hop trusts exactly one address):**
  1. **Existing Caddy:**
     - add `servers { trusted_proxies static <cloudflared's source address>  client_ip_headers Cf-Connecting-Ip X-Forwarded-For }`;
     - the source is expected to be the `proxy` network gateway, 172.20.0.1; this is an estimate to verify;
     - Cloudflare overwrites `Cf-Connecting-Ip` at its edge, so visitors can't forge it;
     - only local processes can reach `127.0.0.1:8080`.
  2. **FinPilot's Caddy:** `trusted_proxies static <existing Caddy's static address on finpilot_edge>`.
  3. **FinPilot's API:** `TRUSTED_PROXIES` = FinPilot's proxy address, as now.
- **Changes to existing services**, all needing your approval, in the production milestone and not the test:
  - one site block and one `servers` option in `/srv/docker/caddy/Caddyfile`;
  - one network entry in `/srv/docker/caddy/compose.yaml`;
  - a Caddy recreate, which briefly interrupts the portfolio and the AI app;
  - a Cloudflare public hostname for FinPilot, if the tunnel has no catch-all rule.

  The `servers` change also gives the existing apps correct client addresses.
- **Alternative B** (not preferred): cloudflared routes FinPilot's hostname straight to a loopback port published by FinPilot's Caddy.
  - It doesn't touch the existing Caddy.
  - But FinPilot would then trust a Docker gateway address and read `Cf-Connecting-Ip` itself, which is the same trust work in a second place.
  - It also adds a second host-name router.
  - Choose it only if you'd rather not edit the existing Caddy.

**P2a changes for behind-proxy mode** (code, next milestone, needs approval):
1. A `FINPILOT_PROXY_MODE=behind` setting that switches the Caddyfile:
   - the site becomes `http://{$FINPILOT_HOST}:8080`;
   - `trusted_proxies static {$FINPILOT_EDGE_IP}`;
   - the redirect site and the ACME settings are dropped;
   - HSTS is kept, along with `-Server`, the 1 MB body limit and the log filter.
2. Compose:
   - the `finpilot_edge` network, internal with static addresses;
   - the proxy's `ports:` limited to direct mode, with nothing published in behind mode;
   - the HTTP default moved off 8080 for direct mode.
3. Tests:
   - deploy-config tests for both modes, with no published port in behind mode and exactly one trusted address;
   - a two-proxy stack test where a test-double edge Caddy inside the stack-test project plays the existing Caddy;
   - a forged `X-Forwarded-For` or `Cf-Connecting-Ip` from a visitor buys no extra sign-in attempts;
   - the Host header is preserved;
   - `__Host-` cookies are set.
4. Docs: the edge site block and `servers` snippet in `deploy/README.md`.

## 16. Confirmed blockers

| ID | Class | Finding | Evidence | Why it matters | Action | Your approval |
|---|---|---|---|---|---|---|
| S-B1 | **BLOCKER** (public launch) | The existing Caddy replaces client addresses | Caddyfile structure: no `trusted_proxies` or `client_ip_headers` | Every visitor shares one sign-in limit, so one attacker could lock everyone out | §15 trust chain | Yes |
| S-B2 | **BLOCKER** (launch) | The container stack has never run | P2a | Images, the sandbox, the proxy and isolation are unproven | The isolated test, §20 | Yes |
| S-B3 | **BLOCKER** (launch) | No verified off-site, encrypted backup for FinPilot; P2a needs `age`, which is missing | Tools list; `server-backup` destination unknown | One laptop and one SSD | §12 (restic) | Yes |
| S-B4 | **BLOCKER** (launch) | No behind-proxy mode | P2a binds its own TLS and port 8080, and 8080 is taken | It can't deploy as designed on this server | §15 changes 1–4 | Yes |
| — | Carried | Beta allowlist (PB-1), legal pages (PB-2), erasure-log retention (PB-3), external monitoring, build provenance (OD-27) | PRODUCTION_READINESS | Launch gates | As planned | Yes |

**None of these blocks the isolated integration test.** It uses its own ports, its own subnet and `tls internal`, and touches no existing service.

## 17. Important improvements

| ID | Finding | Evidence | Action | Approval |
|---|---|---|---|---|
| S-I1 | **IMPORTANT:** the Chromium sandbox will probably fail inside Docker | `kernel.apparmor_restrict_unprivileged_userns=1`; Docker's builtin seccomp profile allows the namespace syscalls only with `CAP_SYS_ADMIN` (estimate), and P2a drops all capabilities and sets `no-new-privileges` | Test it first. If it fails, give **only the api** a seccomp profile that adds `clone`, `clone3` and `unshare` with user namespaces, plus an AppArmor profile allowing `userns`. **Never `--no-sandbox`**, and never `SYS_ADMIN` | Yes |
| S-I2 | **IMPORTANT:** container user IDs collide with host users | web 1000 = the administrator account (sudo, docker, lxd); api 10001 = the AI app's containers; db 999 = `telegrambot` | Move web and api to unused IDs, for example 10201 and 10202, across the Dockerfiles, `init-secrets.sh`, compose and tests. Keep db at 999 (§11) | Yes (code) |
| S-I3 | **IMPORTANT:** Portainer has the Docker socket read-write | mounts | Portainer equals host root, including FinPilot's data. It's on the tailnet only. Keep it updated, use strong admin credentials, or stop it when not in use | Yes |
| S-I4 | **IMPORTANT:** Netdata runs with `pid=host`, `SYS_ADMIN` and `SYS_PTRACE`, unconfined | inspect | It can read any process's memory, FinPilot's included. Standard for Netdata, and tailnet-only. Accept and keep it updated, or drop `SYS_PTRACE` | Yes |
| S-I5 | **IMPORTANT:** the SSD is on every app's critical path, sharing a USB hub with Ethernet | `Requires=`, `lsusb -t` | Check SMART (`sudo smartctl -H -d sat /dev/sda`). Consider moving the Ethernet adapter to the other USB-C port. Keep FinPilot's database on the NVMe | Yes |
| S-I6 | **IMPORTANT:** monitoring can't report a full outage | everything is on the same host | An external check, for example a free UptimeRobot or healthchecks.io, on the public `/readyz` and the backup heartbeat | Yes |
| S-I7 | **IMPORTANT:** reboot pending, and firmware security updates available | §13 | Plan one maintenance window: backup → upgrade → reboot → ME/Thunderbolt/Secure Boot firmware (lid open, AC) → reboot → check that the containers return. Do it **before** the production deploy, not during the test | Yes |
| S-I8 | **IMPORTANT:** existing apps don't come back after a daemon restart | `restart: no`, `live-restore` off | Set `restart: unless-stopped` on them, or confirm the systemd units re-run | Yes |
| S-I9 | **IMPORTANT:** port 9090 is unidentified, on all interfaces | `ss` | Identify it (§9). Disable it if unused | Yes |
| S-I10 | **IMPORTANT** (unverified): a self-hosted GitHub Actions runner on the server | running service | If the portfolio repo is public, make sure fork pull requests can't run on it. The runner isn't in the `docker` group (verified) | Yes |

## 18. Optional improvements

- A battery charge limit of 75–80 % (`charge_control_end_threshold`), given 702 cycles and 76 % health.
- Pin the existing `caddy:2` to an exact version.
- Add memory limits to the existing containers, so none can starve the others.
- Grow the root logical volume if needed: about 135 GB of the volume group is unallocated.
- Cloudflare rate-limiting rules for FinPilot's sign-in path, as a second layer.
- Remove the stopped `portainer-old` and `hesham-portfolio-backup` containers (your decision).

## 19. Unknowns requiring future verification

| Unknown | How to resolve (owner, read-only) |
|---|---|
| UFW rules | `sudo ufw status verbose` |
| What owns port 9090 | `systemctl list-sockets --all \| grep 9090`, or `sudo ss -tlnp 'sport = :9090'` |
| SSD and NVMe SMART health | `sudo smartctl -H -d sat /dev/sda`; `sudo smartctl -H /dev/nvme0` |
| `server-backup` destination, encryption, off-site copy, restores | `systemctl cat server-backup.service` (review it for secrets before sharing) |
| cloudflared ingress rules and catch-all | the Cloudflare dashboard → Tunnels, or the hostnames only from its config |
| The existing Caddy's default `handle`, and its restart policy | `docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' caddy hesham-portfolio portainer` |
| cloudflared's source address as Caddy sees it | the integration milestone, using a test double |
| The AI Document Assistant's peak memory | Netdata history for `ai-backend` |
| The Chromium sandbox under Docker on this kernel | the isolated test (S-I1) |

The inspection script was fixed for the next run:
- the Docker health template no longer fails on containers without a healthcheck;
- the Docker-socket check is repaired;
- `systemctl list-sockets` was added.

## 20. Isolated Docker integration test: plan and GO / NO-GO

### What must be true first (not done yet)

1. **Your approval** for the next milestone. It writes to the server: a copy of the source in a scratch folder, Docker images, and one temporary Compose project.
2. **Local changes, made and gated before the test**, so the test runs what would ship:
   - (a) the user-ID change (S-I2);
   - (b) `stack-test.sh` additions: a before-and-after snapshot of existing containers, networks, volumes and listeners, with an automatic stop if anything outside `finpilot-stacktest` changes; sandbox diagnostics for PDF; and a `docker stats` sample during PDF rendering;
   - (c) behind-proxy mode (§15), if you want it tested in the same run. Otherwise it gets a second test run.
3. A quiet time window. Image builds use several CPU threads for an estimated 5–15 minutes.

### Exact tests to run

These are existing `stack-test.sh` steps that have never been run in containers:
- the `compose config` validation on Compose v5.6.1;
- the three image builds;
- provisioning: owner, role, migrate, apply, verify;
- the 8 configuration refusals;
- the stack tests, including the proxy-only ones: 413, HTTP→HTTPS, and the forged `X-Forwarded-For` sign-in limit;
- isolation: the database and web have no internet, nothing publishes except `127.0.0.1:18443` and `:18080`, and the API runs as its uid;
- a restart of FinPilot's containers only, with no daemon restart;
- persistence;
- backup and restore into a new database, and the refusal to restore over the live one;
- a second deploy and a rollback;
- log scans.

These are new, added in step 2:

| Test | Pass when |
|---|---|
| Chromium sandbox | A PDF export succeeds with `chromium_sandbox=True` under the default profiles. If not, it fails with a recorded reason, then gets the S-I1 profile and a re-test. |
| Read-only root and tmpfs sizes | Chromium runs within `/tmp` and `/home/finpilot` |
| Resource use | Peak memory and CPU per service during 10 PDF exports are recorded and compared with §14 |
| Non-interference | The existing containers' health, restart counts, networks, volumes and listeners are identical before and after |
| Cleanup | No `finpilot-stacktest` container, network or volume remains; the `finpilot-*:stacktest` images are removed by name, with no global prune |
| Behind-proxy mode (if step 2c is done) | Through a test-double edge Caddy: the real client address arrives; forged headers buy nothing; Host is preserved; nothing is published |

### Safest way to run it (for the next milestone, not now)

1. The owner copies only the build-context files to `~/finpilot-stacktest/` on the NVMe, under the home directory, with no root and nothing under `/srv/docker`.
2. Record the before-snapshot: `docker ps -a`, `docker network ls`, `docker volume ls`, `ss -tln`, and the PSI values.
3. Run `timeout 60m deploy/scripts/stack-test.sh --run`. It uses project `finpilot-stacktest`, ports `127.0.0.1:18443/18080`, subnet `172.30.250.0/24` and `tls internal`, with no Cloudflare and no existing Caddy.
4. **Stop immediately if:**
   - memory PSI `some avg10` goes above 5;
   - any existing container changes health or restarts;
   - any listener appears outside loopback.
5. Afterwards: compare against the snapshot, confirm the cleanup, keep the logs and remove the scratch folder.

Never use `docker system prune`, `docker builder prune`, a daemon restart or a reboot during the test.

### Recommendation

**GO for the isolated Docker integration test, once prerequisites 1 and 2 (a, b) are met.** The server has the capacity (verified), and nothing collides with the test's ports, subnet or project names (verified). The test needs no change to any existing service, and no `sudo`.

**NO-GO for public launch** until S-B1 to S-B4 and the carried blockers are closed.

## 21. P4: isolated Docker integration test (2026-11-13)

**Status: prepared and checked locally. Not yet run on the server.** The agent can't use the SSH key non-interactively (§3), so the owner runs the commands in [`deploy/README.md`](../deploy/README.md), "Isolated integration test on the server". Results get recorded here only from the report that run produces.

### What P4 changed before the run

| Item | Change |
|---|---|
| User IDs (S-I2) | api and jobs 10201, web 10202, backup/restore 999:10201. All unused on the host; the preflight re-checks. db stays at 999 (§11) |
| Networks | Every network has a fixed subnet: front, data and egress at 172.30.10–12.x, edge at 172.30.13.0/29. The test uses 172.30.250–253.x |
| Behind-proxy mode (S-B4) | `FINPILOT_PROXY_MODE=behind`: the proxy serves plain HTTP on the internal edge network, publishes nothing, and trusts exactly the edge address, reading right to left. The site is shared with direct mode. Every upstream receives exactly one `X-Forwarded-For` value |
| Chromium sandbox (S-I1) | Confirmed from Docker 29.9.2's own profile that `clone` with namespace flags and `unshare` need `CAP_SYS_ADMIN`, and `chroot` needs `CAP_SYS_CHROOT`. The API now has its own profile: Docker's default plus those three calls, with no capability. The sandbox stays on |
| Backups (S-B3, part) | `backup.sh` and `restore.sh` use restic, which is installed on the server; `age` isn't. If the dump fails part-way, no snapshot is kept |
| Safety | Preflight, a before/after host snapshot, a watchdog, label-only cleanup, an emergency stop, and a bundle builder that refuses anything secret-shaped |

### What the run verifies (pass criteria)

- **Builds:** the three images, with build times and sizes recorded.
- **Database:** provisioning by the real scripts on a disposable database: the owner, the runtime role, migrations to head, RLS applied, and every verify check passing. The 5 startup refusals.
- **Chromium sandbox:** checked three ways:
  - in throwaway containers with no network: Docker's built-in profile (expected to fail), then the API profile (must pass);
  - inside the running API;
  - through 10 PDF exports under load (the per-person limit), and the 11th refused.
- **Direct mode:** all stack tests through the proxy:
  - TLS, the redirect, and the 2.5 MiB body refused with 413;
  - CSRF and Origin, `__Host-` secure cookies, cross-user 404;
  - CSV and PDF exports;
  - the runtime role and RLS;
  - a forged `X-Forwarded-For` buying no sign-in attempts.
- **Resources:** CPU and memory of every container, existing ones included, sampled during the builds and the PDF load.
- **Isolation:**
  - no internet for web and db;
  - web can't reach the database;
  - only `127.0.0.1:18443` and `:18080` are published;
  - user IDs 10201, 10202 and 999;
  - read-only filesystems;
  - the internal edge network and the fixed subnets.
- **Restarts:** restart every container; the data persists.
- **Backup:** an encrypted restic backup that's unreadable with a wrong password, with no plaintext in the repository. Restore into a new database with identical row counts, and refusal of the live database.
- **Releases:** a second deploy, which backs up first, then a rollback.
- **Behind mode:**
  - nothing published;
  - all tests again through a stand-in edge proxy;
  - distinct real clients get separate sign-in allowances through both proxies;
  - forged headers are ignored, both at the edge and when the FinPilot proxy is reached directly;
  - the proxy serves only its own site.
- **Logs:** no query strings, session cookies or database credentials.
- **Cleanup and non-interference:** only test-labelled resources are removed. Comparing the before and after snapshots shows no difference in other containers, networks, volumes, compose projects or TCP listeners.

### Results

Pending the owner's run. Until then, nothing in this section counts as verified in containers.

## 22. P5: production deployment and automatic CI/CD (2026-11-13)

**Status: built and tested locally, pushed to GitHub for the CI and Release runs. Not yet deployed.** The server steps need your SSH passphrase, `sudo`, and your Google, Cloudflare and GitHub accounts. The procedure is [`deploy/server/README.md`](../deploy/server/README.md).

| Decision | Choice and reason |
|---|---|
| Public path | **Tunnel mode:** Cloudflare Tunnel → `cloudflared` → `127.0.0.1:18085` → FinPilot's own proxy. The existing Caddy runs with `admin off`, so changing its routes means restarting it, and with it the portfolio and AI app. The tunnel hostname needs no restart in dashboard-managed mode. Only the egress gateway is trusted, and only for `Cf-Connecting-Ip` |
| Builds and tests | GitHub-hosted runners: CI, then the full container stack test. The very images that passed are published, by commit SHA, to private GHCR |
| Deployment | Pull-based, on the server: a systemd timer, every 2 minutes, with a `read:packages` token only. No GitHub runner or credential ever reaches the server, and the host scripts change only when you re-run `install.sh` |
| Migrations | Automatic when additive. Held for your approval when an `upgrade()` drops, truncates, deletes, rewrites or retypes data. Always preceded by a restic backup |
| Overlaps | `flock` on the server; GitHub concurrency group `release-main`, never cancelled |
| Database | New, empty `finpilot_db_data` on the NVMe. `--first-deploy` refuses if the volume exists. Nothing is copied from `finpilot_dev` |
| Beta | `REGISTRATION_MODE=allowlist` in production by default; `open` is refused at startup. Google consent screen in Testing mode as a second gate |
| Backups | restic nightly at 03:40, with 7 daily, 4 weekly and 6 monthly snapshots, a check, and an Uptime Kuma heartbeat. **No off-site copy:** an open launch risk |
| Monitoring | Uptime Kuma: two HTTP monitors and two push monitors (deploy and health, backup). Netdata sees the containers |
