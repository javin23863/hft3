#!/bin/bash
# SMB export for R|Trader logs from Windows VM on CHI404.
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

WATCH="/root/hft3/rtrader_watch"
SMB_USER="${RTRADER_SMB_USER:-rtrader}"
SMB_PASS="${RTRADER_SMB_PASS:-$(openssl rand -hex 12)}"
LOG_DIR="/root/hft3/logs/rtrader"
mkdir -p "$WATCH" "$LOG_DIR" /etc/samba/smb.conf.d

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq samba samba-common-bin 2>&1 | tee -a "$LOG_DIR/smb_setup.log"

if ! id "$SMB_USER" &>/dev/null; then
  useradd -M -s /usr/sbin/nologin "$SMB_USER"
fi
(echo "$SMB_PASS"; echo "$SMB_PASS") | smbpasswd -s -a "$SMB_USER"
smbpasswd -e "$SMB_USER"

mkdir -p /etc/samba/smb.conf.d
if ! grep -q 'smb.conf.d' /etc/samba/smb.conf; then
  sed -i '/^\[global\]/a \   include = /etc/samba/smb.conf.d/*.conf' /etc/samba/smb.conf
fi
# Remove misplaced includes appended outside [global] (common misconfig).
sed -i '/^include = \/etc\/samba\/smb.conf.d/d' /etc/samba/smb.conf
if ! grep -q 'smb.conf.d' /etc/samba/smb.conf; then
  sed -i '/^\[global\]/a \   include = /etc/samba/smb.conf.d/*.conf' /etc/samba/smb.conf
fi

cat > /etc/samba/smb.conf.d/rtrader_watch.conf <<EOF
[rtrader_watch]
   path = ${WATCH}
   browseable = yes
   read only = no
   guest ok = no
   valid users = ${SMB_USER}
   create mask = 0664
   directory mask = 0775
   force user = root
EOF

systemctl enable smbd nmbd
systemctl restart smbd nmbd

# libvirt default NAT gateway for guest -> host SMB
SMB_HOST_IP="${RTRADER_SMB_HOST:-192.168.122.1}"

grep -q '^RTRADER_SMB_USER=' "$ENV_FILE" 2>/dev/null && \
  sed -i "s|^RTRADER_SMB_USER=.*|RTRADER_SMB_USER=${SMB_USER}|" "$ENV_FILE" || \
  echo "RTRADER_SMB_USER=${SMB_USER}" >> "$ENV_FILE"
grep -q '^RTRADER_SMB_PASS=' "$ENV_FILE" 2>/dev/null && \
  sed -i "s|^RTRADER_SMB_PASS=.*|RTRADER_SMB_PASS=${SMB_PASS}|" "$ENV_FILE" || \
  echo "RTRADER_SMB_PASS=${SMB_PASS}" >> "$ENV_FILE"
grep -q '^RTRADER_SMB_HOST=' "$ENV_FILE" 2>/dev/null && \
  sed -i "s|^RTRADER_SMB_HOST=.*|RTRADER_SMB_HOST=${SMB_HOST_IP}|" "$ENV_FILE" || \
  echo "RTRADER_SMB_HOST=${SMB_HOST_IP}" >> "$ENV_FILE"

echo "SMB share ready: //${SMB_HOST_IP}/rtrader_watch user=${SMB_USER}"
echo "Watch dir: ${WATCH}"
cat > "${WATCH}/rtrader_smb.env" <<EOF
RTRADER_SMB_HOST=${SMB_HOST_IP}
RTRADER_SMB_USER=${SMB_USER}
RTRADER_SMB_PASS=${SMB_PASS}
EOF
chmod 600 "${WATCH}/rtrader_smb.env"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  cat > "${WATCH}/rithmic_login.env" <<EOF
RITHMIC_USERNAME=${RITHMIC_USERNAME:-}
RITHMIC_PASSWORD=${RITHMIC_PASSWORD:-}
RITHMIC_GATEWAY=${RITHMIC_GATEWAY:-Chicago}
RITHMIC_ENVIRONMENT=${RITHMIC_ENVIRONMENT:-Rithmic Paper Trading}
EOF
  chmod 600 "${WATCH}/rithmic_login.env"
fi
testparm -s 2>/dev/null | grep -A5 rtrader_watch || true
