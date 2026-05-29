#!/bin/bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  sysstat \
  linux-tools-common \
  linux-tools-generic \
  numactl \
  ethtool \
  rt-tests \
  chrony \
  git \
  stress-ng \
  python3 \
  jq

KVER=$(uname -r)
apt-get install -y -qq "linux-tools-${KVER}" 2>/dev/null || apt-get install -y -qq linux-tools-generic
ln -sf "/usr/lib/linux-tools/${KVER}/cpupower" /usr/local/bin/cpupower 2>/dev/null || true
command -v cpupower || ls /usr/lib/linux-tools/*/cpupower 2>/dev/null | head -1
echo "Tools installed."
