#!/usr/bin/env bash
# SSH wrapper: sync repo + gate to Vast and launch 230-worker paid screen remotely.
# Requires: VAST_SSH_TARGET (ssh-config alias or user@host) or VAST_SSH_HOST; optional VAST_SSH_PORT.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

VBT_READY_GATE_FILE="${VBT_READY_GATE_FILE:-runtime/reports/paid_screen_ready_gate.json}"

if [[ -n "${VAST_SSH_TARGET:-}" ]]; then
  VAST_SSH_HOST_ARG="$VAST_SSH_TARGET"
elif [[ -n "${VAST_SSH_HOST:-}" ]]; then
  VAST_SSH_HOST_ARG="$VAST_SSH_HOST"
else
  echo "ERROR: set VAST_SSH_TARGET (host or ssh-config alias) or VAST_SSH_HOST (e.g. root@<vast-ip>)" >&2
  echo "       optional VAST_SSH_PORT for non-default SSH port (do not embed -p in VAST_SSH_TARGET)" >&2
  exit 1
fi

SSH_OPTS=(-o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new)
SCP_OPTS=("${SSH_OPTS[@]}")
if [[ -n "${VAST_SSH_PORT:-}" ]]; then
  SSH_OPTS+=(-p "$VAST_SSH_PORT")
  SCP_OPTS+=(-P "$VAST_SSH_PORT")
fi

REMOTE_REPO="${VAST_REMOTE_REPO:-/root/hft3/repo}"
if [[ -z "${VBT_GIT_BRANCH:-}" ]]; then
  BRANCH="$(git branch --show-current)"
  if [[ -z "$BRANCH" ]]; then
    echo "ERROR: detached HEAD with no branch; set VBT_GIT_BRANCH explicitly" >&2
    exit 1
  fi
else
  BRANCH="$VBT_GIT_BRANCH"
fi

echo "Syncing branch $BRANCH to $VAST_SSH_HOST_ARG:${VAST_SSH_PORT:-22} -> $REMOTE_REPO"
ssh "${SSH_OPTS[@]}" "$VAST_SSH_HOST_ARG" "mkdir -p $(dirname "$REMOTE_REPO") && \
  if [[ -d $REMOTE_REPO/.git ]]; then \
    git -C $REMOTE_REPO fetch origin && git -C $REMOTE_REPO checkout $BRANCH && git -C $REMOTE_REPO pull --ff-only origin $BRANCH; \
  else \
    git clone --branch $BRANCH https://github.com/javin23863/hft3.git $REMOTE_REPO; \
  fi"

REMOTE_GATE_DIR="$(dirname "$VBT_READY_GATE_FILE")"
ssh "${SSH_OPTS[@]}" "$VAST_SSH_HOST_ARG" "mkdir -p $REMOTE_REPO/$REMOTE_GATE_DIR"
scp "${SCP_OPTS[@]}" "$VBT_READY_GATE_FILE" \
  "$VAST_SSH_HOST_ARG:$REMOTE_REPO/$VBT_READY_GATE_FILE" 2>/dev/null || true

echo "Launching remote full screen (230 workers on 256 vCPU) in tmux..."
ssh "${SSH_OPTS[@]}" "$VAST_SSH_HOST_ARG" "cd $REMOTE_REPO && \
  tmux new-session -d -s vbt_full 'VBT_READY_GATE_FILE=$VBT_READY_GATE_FILE bash scripts/run_vbt_paid_screen_vast_full.sh; exec bash'"

echo "Attached logs: ssh ${SSH_OPTS[*]} $VAST_SSH_HOST_ARG -t tmux attach -t vbt_full"
