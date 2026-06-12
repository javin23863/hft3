#!/usr/bin/env bash
# Install the hft3-capture-archive systemd service + daily timer on CHI404.
# Prereqs: /root/hft3/repo checkout, rclone configured (hft3-b2 remote), zstd.
set -euo pipefail

REPO=/root/hft3/repo
install -m 0755 "$REPO/infrastructure/chi404/capture_archive.sh" /usr/local/bin/hft3-capture-archive

cat >/etc/systemd/system/hft3-capture-archive.service <<'EOF'
[Unit]
Description=hft3: archive closed Rithmic capture files to B2, prune local
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/hft3-capture-archive
Nice=10
IOSchedulingClass=idle
# keep archival off the isolated trading cores
CPUAffinity=0 1
EOF

cat >/etc/systemd/system/hft3-capture-archive.timer <<'EOF'
[Unit]
Description=hft3: daily capture archival (after CME trade-date roll)

[Timer]
# 18:30 CT == 23:30/00:30 UTC depending on DST; use Chicago-local via OnCalendar TZ
OnCalendar=*-*-* 18:30:00 America/Chicago
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now hft3-capture-archive.timer
systemctl list-timers hft3-capture-archive.timer --no-pager
echo "installed: hft3-capture-archive (daily 18:30 CT)"
