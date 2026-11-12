# FinPilot production on the home server: runbook

Production URL: **https://finpilot.hishamjouda.com** · repository `HishamFekri/FinPilot`, branch `main`.

This is the exact procedure. Steps marked **(you)** need your accounts, your passphrase or `sudo`. Claude can't do them: SSH needs your key's passphrase, and no Google, Cloudflare or GitHub session was shared. Nothing here touches the AI Document Assistant, the portfolio, the existing Caddy, PostgreSQL, Redis or their volumes.

Replace `SERVER_USER@SERVER_TAILSCALE_IP` with your SSH login. Run the PowerShell commands from the FinPilot folder on Windows.

## How it works

```
push to main ──► CI (GitHub-hosted): lint, types, 1,200+ backend tests, web tests, build
                   │ success, push event, this repository only
                   ▼
                 Release (GitHub-hosted): full container stack test ──► push the TESTED images
                   (sandboxed PDFs, RLS, 3 proxy modes,      ghcr.io/hishamfekri/finpilot-{api,web}:sha-<commit>
                    backup/restore, rollback)                then finpilot-release:latest (digests)
                                                               │
home server: finpilot-deploy.timer, every 2 min ◄──────────────┘ pulls (read:packages token only)
  validate the manifest ─► hold if new migrations change or remove data (until you approve)
  ─► deploy.sh: restic backup ─► migrate as owner ─► re-apply and verify RLS ─► api ─► web ─► proxy
  ─► check https://finpilot.hishamjouda.com through Cloudflare ─► roll back the images if it fails
```

Visitors take this path: Cloudflare (TLS) ─► Cloudflare Tunnel ─► `cloudflared` on the host ─► `http://127.0.0.1:18085`, a loopback-only port ─► the FinPilot proxy. The proxy routes `/api/v1/*` to the API and everything else to web. The API uses PostgreSQL through the restricted `finpilot_app` role, with RLS.

**Why no GitHub runner touches the server.** GitHub-hosted runners do every build and test. Nothing from GitHub ever logs in to the server: the server pulls. All GitHub supplies is two image digests, validated line by line. The scripts that run as root on the server are the ones `install.sh` copied from a commit you chose, and they never update themselves. A pull request, a fork or a compromised workflow can't reach your other applications.

**Limits of this design:**
- Code merged into `main` runs inside FinPilot's containers. Those containers are locked down: no Docker socket, read-only filesystems, no capabilities, internal networks.
- If bad code is merged, the protections are:
  - review before merging;
  - the destructive-migration hold;
  - the backup taken before every migration;
  - six months of encrypted backups.

## Isolation

| | FinPilot | Existing apps |
|---|---|---|
| Compose project | `finpilot` (in `/srv/docker/finpilot`) | `ai-document-assistant`, `caddy`, `databases`, … unchanged |
| Database | its own PostgreSQL 18 container, volume `finpilot_db_data` on the internal NVMe | `hesham-postgres` and `hesham-redis`: never connected to |
| Networks | `finpilot_front`, `_data`, `_edge` (internal), `_egress`; 172.30.10–13.x | 172.17–21.x: no overlap |
| Published ports | `127.0.0.1:18085` only | unchanged |
| Backups | restic in `/srv/storage/backups/finpilot/restic` (the external SSD, a different disk from the live data) | unchanged |
| Users | 10201 (api), 10202 (web), 999 (db) | 1000, 10001 … unaffected |

The database volume is on the NVMe rather than `/srv/storage`. If the SSD disconnects, Docker stops for every app (it `Requires=srv-storage.mount`), but the live database can't be swapped for an empty folder. Backups live on the SSD, a different disk from the data.

## 0. GitHub settings (you, once)

1. **Settings › Actions › General:**
   - Workflow permissions: **Read repository contents**, the default; the Release job asks for package-write itself.
   - Fork pull request workflows: **Require approval for all outside collaborators**.
2. **Settings › Branches › Add rule for `main`:**
   - block force pushes and deletion;
   - optionally require the `backend` and `web` checks before merging.
3. **A token for the server:**
   1. Go to **Settings (your account) › Developer settings › Personal access tokens › Tokens (classic)**.
   2. Create one with **only** `read:packages`, expiring in 1 year. (The container registry doesn't accept fine-grained tokens.)
   3. Keep it for step 3.
4. After the first Release run, check that **Packages** shows `finpilot-api`, `finpilot-web` and `finpilot-release` as **Private**.

## 1. Read-only inspection of the existing deployment (you run, Claude reads)

```powershell
cmd /c "ssh -o ConnectTimeout=10 SERVER_USER@SERVER_TAILSCALE_IP bash -s < deploy\inspection\inspect-deploy-setup.sh > %USERPROFILE%\Documents\finpilot-deploy-setup.txt 2>&1"
```

It shows, with secrets redacted:
- how the AI Document Assistant deploys;
- whether cloudflared is managed in the dashboard (token) or by a config file;
- your Caddy routing.

Send me the file path before step 4.

## 2. Google OAuth client (you, Google Cloud Console)

1. **APIs & Services › Credentials › Create credentials › OAuth client ID**, type **Web application**, named "FinPilot production".
2. **Authorized JavaScript origins:** `https://finpilot.hishamjouda.com`
3. **Authorized redirect URIs:** exactly `https://finpilot.hishamjouda.com/api/v1/auth/google/callback`
4. **OAuth consent screen:**
   - External, publishing status **Testing**;
   - add yourself, and later each beta tester, as **test users**. That's a second gate on top of FinPilot's allowlist;
   - scopes: `openid`, `email`, `profile` only.
5. Keep the client ID and client secret for step 3. The secret goes only into a file on the server.

## 3. Install on the server (you)

```powershell
git rev-parse HEAD   # the commit to install; it must be on GitHub main
python deploy\tools\make_bundle.py "$env:USERPROFILE\Documents\finpilot-src.tar.gz"
scp "$env:USERPROFILE\Documents\finpilot-src.tar.gz" SERVER_USER@SERVER_TAILSCALE_IP:finpilot-src.tar.gz
ssh SERVER_USER@SERVER_TAILSCALE_IP "set -e; mkdir -m 700 ~/finpilot-src; tar -xzf ~/finpilot-src.tar.gz -C ~/finpilot-src; rm ~/finpilot-src.tar.gz"
ssh SERVER_USER@SERVER_TAILSCALE_IP "cd ~/finpilot-src && FINPILOT_SNAPSHOT_EXCLUDE=finpilot bash deploy/scripts/host-snapshot.sh --write ~/finpilot-before.txt"
ssh -t SERVER_USER@SERVER_TAILSCALE_IP "sudo bash ~/finpilot-src/deploy/server/install.sh --source ~/finpilot-src --commit FULL_COMMIT_SHA --confirm"
```

Then add the four secrets. Each command opens an editor or waits for a paste followed by Ctrl-D. The values never leave the server.

```powershell
ssh -t SERVER_USER@SERVER_TAILSCALE_IP "sudoedit /srv/docker/finpilot/deploy/finpilot.env"              # FINPILOT_GOOGLE_CLIENT_ID=...
ssh -t SERVER_USER@SERVER_TAILSCALE_IP "sudoedit /srv/docker/finpilot/deploy/secrets/google_client_secret"
ssh -t SERVER_USER@SERVER_TAILSCALE_IP "sudoedit /srv/docker/finpilot/deploy/secrets/beta_allowlist"    # your Google address
ssh -t SERVER_USER@SERVER_TAILSCALE_IP "sudo sh -c 'umask 077; cat > /etc/finpilot/ghcr-token'"         # paste the read:packages token, Enter, Ctrl-D
ssh -t SERVER_USER@SERVER_TAILSCALE_IP "sudo cat /etc/finpilot/restic-password"                         # copy into your password manager now
```

## 4. Cloudflare Tunnel public hostname (you, after step 1 shows how the tunnel is managed)

- **Managed in the dashboard (the unit runs `cloudflared tunnel run --token …`):**
  1. Go to Zero Trust › Networks › Tunnels › your tunnel › **Public Hostname › Add**.
  2. Subdomain `finpilot`, domain `hishamjouda.com`, service type **HTTP**, URL `127.0.0.1:18085`.
  3. Save.

  Cloudflare creates the DNS record. The other hostnames aren't touched, and nothing restarts.
- **Managed by a local config file:**
  1. Add `- hostname: finpilot.hishamjouda.com` with `service: http://127.0.0.1:18085`, **above** the catch-all rule.
  2. Run `cloudflared tunnel route dns <tunnel> finpilot.hishamjouda.com`.
  3. Restart `cloudflared`.

  The restart briefly interrupts every site behind the tunnel, so do it at a quiet moment. Claude won't do it.
- Use `127.0.0.1`, not `localhost`: the port listens on IPv4 loopback only.
- Leave **SSL/TLS › Edge Certificates › Always Use HTTPS** on. The app sends HSTS, and it serves only this host name.

## 5. First deployment (you)

This needs a successful Release run on GitHub for the installed commit.

```powershell
ssh -t SERVER_USER@SERVER_TAILSCALE_IP "sudo systemd-run --wait --pty -p EnvironmentFile=/etc/finpilot/deployer.env /srv/docker/finpilot/deploy/server/finpilot-deployer.sh --first-deploy"
```

It refuses if `finpilot_db_data` already exists, so it can't provision over data. Otherwise it:
1. provisions the **empty** production database: the owner, the restricted runtime role, migrations, RLS and verification;
2. deploys;
3. checks the public URL.

Then enable the timers, and compare the rest of the host with the snapshot from step 3:

```powershell
ssh -t SERVER_USER@SERVER_TAILSCALE_IP "sudo systemctl enable --now finpilot-deploy.timer finpilot-backup.timer"
ssh SERVER_USER@SERVER_TAILSCALE_IP "cd ~/finpilot-src && FINPILOT_SNAPSHOT_EXCLUDE=finpilot bash deploy/scripts/host-snapshot.sh --write ~/finpilot-after.txt && bash deploy/scripts/host-snapshot.sh --compare ~/finpilot-before.txt ~/finpilot-after.txt"
```

The comparison should show `SAME` for everything except one new TCP listener, `127.0.0.1:18085`.

## 6. Monitoring (you, in Uptime Kuma)

| Monitor | Type | Target | Alert |
|---|---|---|---|
| FinPilot API ready | HTTP(s), keyword `ready` | `https://finpilot.hishamjouda.com/api/v1/readyz` | your existing notification (Telegram, if configured) |
| FinPilot web | HTTP(s) | `https://finpilot.hishamjouda.com/en` | same |
| FinPilot deploy and health | **Push**, heartbeat 10 min | URL into `/etc/finpilot/kuma-deploy-url` | down on an unhealthy container, a restart loop, a failed or held release |
| FinPilot backup | **Push**, heartbeat 26 h | URL into `/etc/finpilot/kuma-backup-url` | down when the nightly backup fails or doesn't run |

Netdata already picks up the FinPilot containers' CPU and memory. The deploy timer reports every 2 minutes:
- health: every container healthy, no restart loop, the public `readyz` answering;
- a down heartbeat with the reason, when something fails.

## 7. Verification checklist (you, with a real Google account)

1. Sign in with your allowlisted account. Then try an account that isn't on the list: it's refused with "private beta", and no account is created.
2. Create an account and a transaction. Open Reports; export CSV, then PDF in Arabic and Turkish. Sign out.
3. Back up and restore into an isolated database, never over the live one:
   ```bash
   sudo systemctl start finpilot-backup.service && journalctl -u finpilot-backup -n 20 --no-pager
   cd /srv/docker/finpilot/deploy && sudo RESTIC_CACHE_DIR=/var/cache/finpilot-restic scripts/restore.sh --from-restic SNAPSHOT --restic-repo /srv/storage/backups/finpilot/restic --restic-password-file /etc/finpilot/restic-password --into finpilot_restore_drill_YYYYMMDD --confirm
   ```
4. Persistence: `cd /srv/docker/finpilot/deploy && sudo docker compose -p finpilot restart db api`, then sign in again. Your records are still there.
5. Automatic deployment: push a small documentation change to `main`, then watch:
   - CI;
   - Release;
   - `journalctl -u finpilot-deploy -f`, which should show `release <sha> is live`.

## Approving a held release

When the deployer logs `HELD: new migrations change or remove data`:
1. Read the listed migration files at that commit on GitHub.
2. To proceed, run `sudo touch /etc/finpilot/approved/<full sha>`. The next cycle deploys it, after a fresh backup.

## Rollback

| Situation | Do this |
|---|---|
| A new release fails its health or public check | Automatic: the previous images are put back, and that release isn't retried |
| You want the previous release back | `sudo touch /etc/finpilot/hold` (pauses automatic deploys), then `cd /srv/docker/finpilot/deploy && sudo scripts/rollback.sh --confirm`. Remove the hold file once a fixed release is published |
| Pause all deployments | `sudo touch /etc/finpilot/hold`. Health checks continue |
| Bad data after a migration | Restore the pre-migration snapshot into a new database (`restore.sh --from-restic`, above). Check it, then switch as described in `deploy/README.md` › Rollback. Never restore over the live database |
| Take FinPilot offline | Remove the tunnel's public hostname (Cloudflare), or `sudo docker compose -p finpilot stop`. Data volumes stay |
| Remove the deployer | `sudo systemctl disable --now finpilot-deploy.timer finpilot-backup.timer` |

## Updating the server-side scripts

`/srv/docker/finpilot/deploy` changes only when you re-run step 3's `install.sh` with a newer commit. Do that whenever a release changes anything under `deploy/` (compose files, Caddy, scripts). Images update automatically; the host scripts update only when you choose.

## Still open

These are launch risks, not deployment steps:
- **Off-site backups:** none is configured. The restic repository is on the same machine.
- **Legal review:** the privacy notice and terms are a labelled draft. Until they're reviewed, public registration stays impossible: production refuses `REGISTRATION_MODE=open`.
- **The stack test on the home server itself.** The GitHub runner run covers the containers, but not the server's own kernel and AppArmor settings for the Chromium sandbox. The first deployment's PDF export is the check there.
