#!/bin/bash

# Chicago CME Microstructure - Clock Sync Setup (chrony/PTP)
# Must be run as root

echo "Installing chrony..."
apt-get update
apt-get install -y chrony

# Configure chrony for high-precision timekeeping
CHRONY_CONF="/etc/chrony/chrony.conf"
cp $CHRONY_CONF "${CHRONY_CONF}.bak"

# Use local stratum 1 PTP/NTP servers if available in Aurora/Chicago colocation.
# For standard internet, we use pool servers, but we configure aggressive polling.
cat << 'EOF' > $CHRONY_CONF
# High-precision CME colocation chrony config

# Use Chicago-area NTP pools (replace with direct colocation PTP/NTP IPs when live)
server 0.us.pool.ntp.org iburst maxpoll 4
server 1.us.pool.ntp.org iburst maxpoll 4
server 2.us.pool.ntp.org iburst maxpoll 4
server 3.us.pool.ntp.org iburst maxpoll 4

# Record the rate at which the system clock gains/losses time
driftfile /var/lib/chrony/chrony.drift

# Allow the system clock to be stepped in the first three updates if its offset is larger than 1 second.
makestep 1 3

# Enable hardware timestamping on all interfaces
hwtimestamp *

# Minimize clock jitter
maxupdateskew 100.0
corrtimeratio 100

# Specify directory for log files
logdir /var/log/chrony
log measurements statistics tracking
EOF

echo "Restarting chrony..."
systemctl restart chrony
systemctl enable chrony

echo "Clock sync configured. Verify with 'chronyc tracking' and 'chronyc sources'"
