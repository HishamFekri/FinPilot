#!/usr/bin/env bash
# FinPilot P3: read-only home server inspection.
#
# Run it from Windows (cmd keeps the bytes exact; PowerShell 5.1 has no "<"):
#   cmd /c "ssh -o ConnectTimeout=10 SERVER_USER@SERVER_TAILSCALE_IP bash -s < deploy\inspection\inspect-server.sh > %USERPROFILE%\Documents\finpilot-server-inspection.txt 2>&1"
#
# What it does: only reads system state. No sudo, no package changes, no
# container start/stop, no writes outside the terminal, no network scans.
# What it never prints: environment variables, container env or labels,
# .env or compose files, secrets, backup contents, process arguments,
# Tailscale peers, crontab command lines, or reverse-proxy lines that could
# hold credentials. Anything credential-shaped is replaced by <redacted>.
#
# Every command is wrapped in "timeout" and failures are reported, not fatal.

set -u
export LC_ALL=C
T=20

section() { printf '\n===== %s =====\n' "$1"; }
run() {  # run LABEL CMD...  (read-only commands only)
  printf -- '--- %s\n' "$1"; shift
  timeout "$T" "$@" 2>&1 || printf '(exit %s)\n' "$?"
}
have() { command -v "$1" >/dev/null 2>&1; }
redact() {
  sed -E \
    -e 's/((pass(word)?|passwd|secret|token|api[_-]?key|apikey|auth|credentials?|bearer|basic_?auth|private[_-]?key)[^ =:]*[ =:]+)[^ ]+/\1<redacted>/Ig' \
    -e 's#(postgres(ql)?|redis|mysql|mongodb)(\+[a-z0-9]+)?://[^ ]+#\1://<redacted>#Ig' \
    -e 's/(tskey|cf|ghp|gho|sk|pk)[-_][A-Za-z0-9_-]{12,}/<redacted>/g' \
    -e 's/\$(2[aby]?|argon2(id|i|d)?|[156])\$[^ ]+/<hash>/g' \
    -e 's/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/<email>/g'
}

# Second line of defence for everything printed: credentials inside URLs,
# well-known token prefixes, password hashes and email addresses.
redact_all() {
  sed -E \
    -e 's#([a-z][a-z0-9+.-]*://)[^/@ ]+:[^/@ ]+@#\1<redacted>@#Ig' \
    -e 's/(tskey|ghp|gho|github_pat|glpat|xox[bap])[-_][A-Za-z0-9_-]{12,}/<redacted>/g' \
    -e 's/\$(2[aby]?|argon2(id|i|d)?|[156])\$[^ ]+/<hash>/g' \
    -e 's/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/<email>/g'
}
exec > >(redact_all) 2>&1

printf 'FinPilot server inspection\n'
run "date (UTC)" date -u '+%Y-%m-%dT%H:%M:%SZ'
run "who am I" id

section "1. System"
run "os-release" grep -E '^(PRETTY_NAME|VERSION_ID|VERSION_CODENAME)=' /etc/os-release
run "kernel" uname -srvmo
run "uptime" uptime
run "boot time" uptime -s
run "hardware model" cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_version /sys/class/dmi/id/product_name
run "bios" cat /sys/class/dmi/id/bios_version /sys/class/dmi/id/bios_date
run "lscpu" lscpu
run "virtualization" systemd-detect-virt

section "2. Memory, load and pressure"
run "free" free -h
run "meminfo" grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree|Committed_AS|CommitLimit|Dirty|Shmem):' /proc/meminfo
run "swap devices" swapon --show
run "swappiness" cat /proc/sys/vm/swappiness
run "overcommit" cat /proc/sys/vm/overcommit_memory
run "loadavg" cat /proc/loadavg
run "vmstat 1s x5" vmstat -w 1 5
for p in cpu memory io; do run "pressure $p" cat "/proc/pressure/$p"; done
run "top 15 processes by memory (name only, no arguments)" sh -c 'ps -eo pid,user:12,comm,%cpu,%mem,rss --sort=-rss | head -16'
run "top 10 processes by cpu (name only)" sh -c 'ps -eo pid,user:12,comm,%cpu,%mem --sort=-%cpu | head -11'
run "oom kills this boot (kernel log, if readable)" sh -c 'journalctl -k -b --no-pager -q 2>/dev/null | grep -iE "out of memory|oom-kill|killed process" | tail -10'

section "3. Thermal and power"
for z in /sys/class/thermal/thermal_zone*; do
  [ -r "$z/temp" ] && printf '%s %s %s\n' "$(basename "$z")" "$(cat "$z/type" 2>/dev/null)" "$(cat "$z/temp" 2>/dev/null)"
done
have sensors && run "sensors" sensors
for b in /sys/class/power_supply/*; do
  [ -d "$b" ] || continue
  printf -- '--- power_supply %s\n' "$(basename "$b")"
  for f in type status online capacity capacity_level cycle_count energy_full energy_full_design charge_full charge_full_design charge_control_start_threshold charge_control_end_threshold health; do
    [ -r "$b/$f" ] && printf '%s=%s\n' "$f" "$(cat "$b/$f" 2>/dev/null)"
  done
done
run "lid and sleep handling" sh -c 'grep -hE "^(HandleLidSwitch|HandleLidSwitchExternalPower|HandleLidSwitchDocked|IdleAction)" /etc/systemd/logind.conf /etc/systemd/logind.conf.d/*.conf 2>/dev/null; systemctl is-enabled sleep.target suspend.target hibernate.target hybrid-sleep.target 2>&1'
run "ac power / upower" sh -c 'have=$(command -v upower) && upower -d 2>/dev/null | grep -E "native-path|state|percentage|capacity|on-battery|warning-level|time to" | head -30'
have tlp-stat && run "tlp battery thresholds" sh -c 'tlp-stat -b 2>/dev/null | grep -iE "threshold|charge_control|status" | head -12'

section "4. Updates, reboot, firmware"
run "reboot required" sh -c 'if [ -f /var/run/reboot-required ]; then echo YES; cat /var/run/reboot-required.pkgs 2>/dev/null; else echo no; fi'
run "upgradable packages (from local cache, no refresh)" sh -c 'apt list --upgradable 2>/dev/null | tail -n +2 | wc -l; apt list --upgradable 2>/dev/null | tail -n +2 | grep -iE "security|linux-|docker|containerd|tailscale|caddy|nginx|openssh|postgres" | head -40'
[ -x /usr/lib/update-notifier/apt-check ] && run "apt-check" /usr/lib/update-notifier/apt-check --human-readable
run "unattended upgrades" sh -c 'cat /etc/apt/apt.conf.d/20auto-upgrades 2>/dev/null; grep -E "Automatic-Reboot" /etc/apt/apt.conf.d/50unattended-upgrades 2>/dev/null | grep -v "^//"'
run "last apt activity" sh -c 'ls -l --time-style=+%F /var/log/apt/history.log 2>/dev/null'
run "installed kernels" sh -c 'ls /boot | grep -E "^vmlinuz-" '
have fwupdmgr && run "firmware updates (no metadata refresh)" fwupdmgr get-updates --no-metadata-check --no-unreported-check --no-reboot-check

section "5. Storage"
run "lsblk" lsblk -o NAME,SIZE,TYPE,FSTYPE,FSUSE%,MOUNTPOINTS,TRAN,ROTA,MODEL
run "df" df -hT -x tmpfs -x devtmpfs -x squashfs -x overlay
run "df inodes" df -i -x tmpfs -x devtmpfs -x squashfs -x overlay
run "findmnt /" findmnt -no SOURCE,FSTYPE,OPTIONS /
run "findmnt /boot /boot/efi" sh -c 'findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /boot; findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /boot/efi'
run "findmnt /srv/storage" findmnt -no SOURCE,FSTYPE,OPTIONS,TARGET /srv/storage
run "is /srv/storage a separate mount" mountpoint /srv/storage
run "fstab (credentials redacted)" sh -c 'grep -vE "^\s*(#|$)" /etc/fstab | sed -E "s/(credentials|password|username|user)=[^, ]+/\1=<redacted>/g"'
run "systemd mount units" systemctl list-units --type=mount --all --no-pager --no-legend
run "srv-storage mount unit" sh -c 'systemctl show srv-storage.mount -p ActiveState,Result,What,Where,Options,Before,WantedBy,RequiredBy 2>&1'
run "top-level directories in /srv/storage (names only)" sh -c 'find /srv/storage -mindepth 1 -maxdepth 1 -type d -printf "%M %u:%g %f\n" 2>&1 | head -40'
run "top-level of /srv" sh -c 'find /srv -mindepth 1 -maxdepth 1 -printf "%M %u:%g %f\n" 2>&1'
run "usb storage and link speed" sh -c 'for d in /sys/block/sd*; do [ -e "$d" ] || continue; n=$(basename $d); printf "%s removable=%s " $n "$(cat $d/removable)"; readlink -f $d/device | grep -oE "usb[0-9]+/[0-9.-]+" | head -1; done; command -v lsusb >/dev/null && lsusb -t 2>/dev/null'
run "disk errors this boot (kernel log, if readable)" sh -c 'journalctl -k -b --no-pager -q 2>/dev/null | grep -iE "i/o error|uas|reset (high|super)speed|disconnect|ext4-fs (error|warning)|btrfs.*error|xfs.*error|nvme.*(timeout|error)|blk_update_request" | tail -25'
run "disk errors previous boot" sh -c 'journalctl -k -b -1 --no-pager -q 2>/dev/null | grep -iE "i/o error|disconnect|ext4-fs error|nvme.*error" | tail -10'
run "smart (only if readable without root)" sh -c 'command -v smartctl >/dev/null && for d in /dev/nvme0 /dev/sda /dev/sdb; do [ -e $d ] && smartctl -H $d 2>&1 | tail -3; done'
run "fsck history" sh -c 'for d in $(lsblk -rno PATH,FSTYPE | awk "\$2==\"ext4\"{print \$1}"); do echo "$d"; tune2fs -l "$d" 2>&1 | grep -E "Filesystem state|Last checked|Mount count|Lifetime writes" ; done'

section "6. Docker"
if have docker; then
  run "docker version" docker version --format 'client {{.Client.Version}} server {{.Server.Version}} api {{.Server.APIVersion}}'
  run "compose version" docker compose version
  run "docker info" docker info --format 'root={{.DockerRootDir}} driver={{.Driver}} cgroup={{.CgroupDriver}} v{{.CgroupVersion}} security={{.SecurityOptions}} containers={{.Containers}} running={{.ContainersRunning}} images={{.Images}} cpus={{.NCPU}} mem={{.MemTotal}} live_restore={{.LiveRestoreEnabled}} logging={{.LoggingDriver}} warnings={{.Warnings}}'
  run "daemon.json (keys only)" sh -c 'test -r /etc/docker/daemon.json && grep -oE "\"[a-z-]+\"\s*:" /etc/docker/daemon.json | sort -u'
  run "docker service" systemctl show docker -p ActiveState,UnitFileState,After,Requires,RequiresMountsFor
  run "containers" docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.Label "com.docker.compose.project"}}'
  run "compose projects (paths only)" docker compose ls -a
  run "container stats (one sample)" docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.PIDs}}\t{{.BlockIO}}'
  ids=$(docker ps -aq)
  if [ -n "$ids" ]; then
    run "per-container settings (no env, no labels)" docker inspect --format '{{.Name}} restart={{.HostConfig.RestartPolicy.Name}} mem={{.HostConfig.Memory}} cpus={{.HostConfig.NanoCpus}} pids={{.HostConfig.PidsLimit}} privileged={{.HostConfig.Privileged}} net={{.HostConfig.NetworkMode}} pid={{.HostConfig.PidMode}} user="{{.Config.User}}" ro={{.HostConfig.ReadonlyRootfs}} capadd={{.HostConfig.CapAdd}} secopt={{.HostConfig.SecurityOpt}} health={{with index .State "Health"}}{{index . "Status"}}{{else}}none{{end}} restarts={{.RestartCount}} started={{.State.StartedAt}}' $ids
    run "mounts (type, source, destination)" docker inspect --format '{{.Name}}{{range .Mounts}} | {{.Type}} {{if .Name}}{{.Name}}{{else}}{{.Source}}{{end}} -> {{.Destination}} rw={{.RW}}{{end}}' $ids
    echo '--- docker socket mounted into'
    timeout "$T" docker inspect --format '{{.Name}}{{range .Mounts}} {{.Source}}{{end}}' $ids 2>&1 | grep -E 'docker\.sock' || echo none
  fi
  run "networks" docker network ls --format 'table {{.Name}}\t{{.Driver}}\t{{.Scope}}'
  run "network subnets" sh -c 'for n in $(docker network ls -q); do docker network inspect --format "{{.Name}} internal={{.Internal}} {{range .IPAM.Config}}{{.Subnet}} {{end}}" $n; done'
  run "volumes" docker volume ls --format 'table {{.Name}}\t{{.Driver}}\t{{.Label "com.docker.compose.project"}}'
  run "disk usage" docker system df
  run "any finpilot leftovers" sh -c 'docker ps -a --format "{{.Names}}" | grep -i finpilot; docker network ls --format "{{.Name}}" | grep -i finpilot; docker volume ls -q | grep -i finpilot; echo "(empty above means none)"'
  run "image architecture" docker info --format '{{.Architecture}} {{.OSType}}'
else
  echo "docker CLI not found for this user"
fi
run "docker group membership" sh -c 'getent group docker'
run "docker socket permissions" ls -l /var/run/docker.sock
run "rootless docker" sh -c 'systemctl --user is-active docker 2>&1'

section "7. Sandbox prerequisites for Chromium in containers"
for k in kernel.unprivileged_userns_clone kernel.apparmor_restrict_unprivileged_userns user.max_user_namespaces kernel.seccomp.actions_avail; do
  run "$k" sysctl -n "$k"
done
run "apparmor enabled" cat /sys/module/apparmor/parameters/enabled
run "seccomp support in kernel (actions above); this shell is unfiltered" sh -c 'grep -E "^Seccomp" /proc/self/status'

section "8. Network"
run "interfaces" ip -br addr
run "links" ip -br link
run "routes" ip route
run "default route" ip route get 1.1.1.1
run "wifi state" sh -c 'command -v nmcli >/dev/null && nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device 2>/dev/null; command -v networkctl >/dev/null && networkctl list --no-pager 2>/dev/null'
run "listening tcp (no process names without root)" ss -tlnH
run "listening udp" ss -ulnH
run "systemd sockets (shows socket-activated listeners such as 9090)" systemctl list-sockets --all --no-pager
run "who binds 80 and 443" sh -c 'ss -tlnH "( sport = :80 or sport = :443 or sport = :8080 or sport = :8443 or sport = :18080 or sport = :18443 )"'
run "database and cache ports" sh -c 'ss -tlnH "( sport = :5432 or sport = :5433 or sport = :6379 )"'
run "dns resolver" sh -c 'resolvectl status 2>/dev/null | grep -E "DNS Servers|Current DNS|DNSSEC" | head -6'

section "9. Firewall (status readable without root only partly)"
run "ufw service" systemctl show ufw -p ActiveState,UnitFileState
run "ufw enabled flag" sh -c 'grep -E "^ENABLED=" /etc/ufw/ufw.conf'
run "ufw rules files (rule lines only)" sh -c 'grep -hE "^### tuple" /etc/ufw/user.rules /etc/ufw/user6.rules 2>&1 | head -40'
run "ufw default policies" sh -c 'grep -E "^DEFAULT_(INPUT|FORWARD|OUTPUT)_POLICY" /etc/default/ufw'
run "docker and ufw (ufw-docker hooks present?)" sh -c 'grep -c "DOCKER-USER" /etc/ufw/after.rules 2>&1'
run "nftables service" systemctl show nftables -p ActiveState,UnitFileState
run "fail2ban" sh -c 'systemctl is-active fail2ban 2>&1'

section "10. Tailscale (self only, no peers)"
if have tailscale; then
  run "version" tailscale version
  run "self" tailscale status --self=true --peers=false
  run "serve (exposed to tailnet)" tailscale serve status
  run "funnel (exposed to the internet)" tailscale funnel status
else
  echo "tailscale CLI not found"
fi
run "tailscaled" systemctl show tailscaled -p ActiveState,UnitFileState

section "11. Public exposure paths"
run "cloudflared" sh -c 'systemctl is-active cloudflared 2>&1; docker ps --format "{{.Names}} {{.Image}}" 2>/dev/null | grep -iE "cloudflared|tunnel|ngrok|frp" || echo "no tunnel containers"'
run "public IPv6 addresses (scope global)" sh -c 'ip -6 addr show scope global | grep -oE "inet6 [0-9a-f:]+/[0-9]+" | sed -E "s/:[0-9a-f]+:[0-9a-f]+:[0-9a-f]+:[0-9a-f]+\//:xxxx:xxxx:xxxx:xxxx\//"'
run "upnp clients" sh -c 'command -v upnpc || echo none'

section "12. SSH server (non-secret settings)"
run "sshd settings" sh -c 'grep -hiE "^\s*(Port|ListenAddress|PermitRootLogin|PasswordAuthentication|KbdInteractiveAuthentication|PubkeyAuthentication|AllowUsers|AllowGroups|MaxAuthTries)\b" /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>&1'
run "ssh service" sh -c 'systemctl show ssh -p ActiveState,UnitFileState; systemctl is-active ssh.socket 2>&1'
run "my authorized key count (no key material)" sh -c 'test -r ~/.ssh/authorized_keys && grep -cvE "^\s*(#|$)" ~/.ssh/authorized_keys'

section "13. Reverse proxies"
for svc in caddy nginx apache2 traefik haproxy; do run "service $svc" systemctl show "$svc" -p ActiveState,UnitFileState,MainPID; done
run "proxy containers" sh -c 'docker ps --format "{{.Names}} {{.Image}} {{.Ports}}" 2>/dev/null | grep -iE "caddy|nginx|traefik|haproxy|proxy" || echo none'
run "caddy config files (paths only)" sh -c 'find /etc/caddy /srv /opt "$HOME" -maxdepth 4 -name "Caddyfile*" -type f 2>/dev/null | head -20'
for f in /etc/caddy/Caddyfile $(find /srv /opt "$HOME" -maxdepth 4 -name 'Caddyfile' -type f 2>/dev/null | head -5); do
  [ -r "$f" ] || continue
  printf -- '--- caddy structure %s (site labels, routes, tls mode only)\n' "$f"
  grep -nE '^\S[^#]*\{\s*$|^\s*(reverse_proxy|handle(_path)?|route|redir|bind|tls|import|encode|header\s+-?Strict|request_body|rate_limit|forward_auth|basicauth|basic_auth|admin|auto_https|email|servers|trusted_proxies)\b' "$f" \
    | sed -E -e 's/(basic_?auth[^{]*)\{.*/\1{ <redacted> }/I' -e 's/^(\s*[0-9]+:\s*(email)\s+).*/\1<redacted>/I' -e 's/(tls\s+)[^ ]+@[^ ]+/\1<email>/' | redact
done
run "nginx enabled sites (names only)" sh -c 'ls /etc/nginx/sites-enabled /etc/nginx/conf.d 2>&1'
for f in /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf; do
  [ -r "$f" ] || continue
  printf -- '--- nginx structure %s\n' "$f"
  grep -nE '^\s*(listen|server_name|proxy_pass|location|ssl_certificate\b|client_max_body_size|limit_req|return\s+30[18])' "$f" | sed -E 's#(ssl_certificate\s+).*#\1<path>#' | redact
done
run "nginx listen summary" sh -c 'grep -rhoE "^\s*listen\s+[^;]+" /etc/nginx 2>/dev/null | sort | uniq -c'

section "14. Databases and caches on the host"
for svc in postgresql redis-server redis; do run "service $svc" systemctl show "$svc" -p ActiveState,UnitFileState; done
run "postgres/redis containers" sh -c 'docker ps -a --format "{{.Names}} {{.Image}} {{.Status}} {{.Ports}}" 2>/dev/null | grep -iE "postgres|pgvector|timescale|redis|valkey|mariadb|mysql|mongo" || echo none'
run "host postgres clusters" sh -c 'command -v pg_lsclusters >/dev/null && pg_lsclusters 2>&1'

section "15. Monitoring and alerting"
run "monitoring containers" sh -c 'docker ps --format "{{.Names}} {{.Image}} {{.Status}} {{.Ports}}" 2>/dev/null | grep -iE "kuma|netdata|portainer|prometheus|grafana|loki|watchtower|diun|healthcheck|gotify|ntfy" || echo none'
run "monitoring services" sh -c 'for s in netdata uptime-kuma prometheus-node-exporter; do printf "%s " $s; systemctl is-active $s 2>&1; done'
run "watchtower or auto-update" sh -c 'docker ps --format "{{.Names}} {{.Image}}" 2>/dev/null | grep -iE "watchtower|ouroboros" || echo none'

section "16. Scheduled jobs and backups (names and schedules only)"
run "systemd timers" systemctl list-timers --all --no-pager
run "my crontab (schedule and program only)" sh -c 'crontab -l 2>/dev/null | grep -vE "^\s*(#|$)" | awk "{printf \"%s %s %s %s %s  %s\n\", \$1,\$2,\$3,\$4,\$5,\$6}"'
run "system cron files (names only)" sh -c 'ls /etc/cron.d /etc/cron.daily /etc/cron.weekly 2>&1'
run "backup tooling available" sh -c 'for t in age gpg restic borg rclone rsync zstd pg_dump psql sqlite3 duplicity kopia; do printf "%-10s %s\n" $t "$(command -v $t || echo -)"; done'
run "backup-looking directories (names and sizes from df only)" sh -c 'for d in /srv/storage/backups /srv/storage/backup /srv/backups /var/backups "$HOME/backups"; do [ -d "$d" ] && printf "%s exists, on: %s\n" "$d" "$(df -P "$d" | awk "NR==2{print \$1\" \"\$6}")"; done'
run "backup services" sh -c 'systemctl list-unit-files --no-pager --no-legend 2>/dev/null | grep -iE "backup|restic|borg|rclone|kopia|duplicati" || echo none'
run "backup containers" sh -c 'docker ps -a --format "{{.Names}} {{.Image}} {{.Status}}" 2>/dev/null | grep -iE "backup|restic|borg|duplicati|kopia|rclone" || echo none'

section "17. Running services"
run "running services" systemctl list-units --type=service --state=running --no-pager --no-legend
run "failed units" systemctl --failed --no-pager --no-legend
run "enabled but not running" sh -c 'systemctl list-unit-files --type=service --state=enabled --no-pager --no-legend | head -60'

section "18. FinPilot fit checks"
run "free on the docker root" sh -c 'df -h "$(docker info --format "{{.DockerRootDir}}" 2>/dev/null || echo /var/lib/docker)" 2>&1'
run "free on /srv/storage" df -h /srv/storage
run "subnet 172.30.10.0/24 or 172.30.250.0/24 already routed?" sh -c 'ip route | grep -E "172\.30\.(10|250)\." || echo "not in use"'
run "loopback ports FinPilot would use" sh -c 'ss -tlnH | grep -E ":(8080|8443|18080|18443)\b" || echo free'
run "uid 10001 and 999 on host" sh -c 'getent passwd 10001 999 || echo "no host users with those ids"'

printf '\n===== done =====\n'
