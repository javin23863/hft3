#!/usr/bin/env bash
# SSH wrapper: sync repo + gate to Vast and launch 230-worker paid screen remotely.
# Requires: VAST_SSH_HOST (or VAST_SSH alias in ~/.ssh/config), NPZ already on Vast.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

VAST_SSH_TARGET="${VAST_SSH_TARGET:-${VAST_SSH_HOST:-}}"
if [[ -z "$VAST_SSH_TARGET" ]]; then
  echo "ERROR: set VAST_SSH_TARGET or VAST_SSH_HOST (e.g. root@<vast-ip> -p <port>)" >&2
  exit 1
fi

REMOTE_REPO="${VAST_REMOTE_REPO:-/root/hft3/repo}"
BRANCH="${VBT_GIT_BRANCH:-codex/vbt-hbt-handoff}"
SSH_OPTS=(-o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new)

echo "Syncing branch $BRANCH to $VAST_SSH_TARGET:$REMOTE_REPO"
ssh "${SSH_OPTS[@]}" "$VAST_SSH_TARGET" "mkdir -p $(dirname "$REMOTE_REPO") && \
  if [[ -d $REMOTE_REPO/.git ]]; then \
    git -C $REMOTE_REPO fetch origin && git -C $REMOTE_REPO checkout $BRANCH && git -C $REMOTE_REPO pull --ff-only origin $BRANCH; \
  else \
    git clone --branch $BRANCH https://github.com/javin23863/hft3.git $REMOTE_REPO; \
  fi"

scp "${SSH_OPTS[@]}" runtime/reports/paid_screen_ready_gate.json \
  "$VAST_SSH_TARGET:$REMOTE_REPO/runtime/reports/paid_screen_ready_gate.json" 2>/dev/null || true

echo "Launching remote full screen (230 workers on 256 vCPU) in tmux..."
ssh "${SSH_OPTS[@]}" "$VAST_SSH_TARGET" "cd $REMOTE_REPO && \
  tmux new-session -d -s vbt_full 'bash scripts/run_vbt_paid_screen_vast_full.sh; exec bash'"

echo "Attached logs: ssh $VAST_SSH_TARGET -t tmux attach -t vbt_full"
