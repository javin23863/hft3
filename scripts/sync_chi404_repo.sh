#!/bin/bash
# Clone or update hft3 git repo on CHI404 at /root/hft3/repo
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

REPO_URL="${HFT3_GIT_URL:-https://github.com/javin23863/hft3.git}"
REPO_DIR="${HFT3_REPO_DIR:-/root/hft3/repo}"
BRANCH="${HFT3_GIT_BRANCH:-main}"

mkdir -p /root/hft3

if [[ -d "$REPO_DIR/.git" ]]; then
  echo "Updating $REPO_DIR"
  git -C "$REPO_DIR" fetch origin
  git -C "$REPO_DIR" checkout "$BRANCH"
  git -C "$REPO_DIR" pull --ff-only origin "$BRANCH"
else
  echo "Cloning $REPO_URL -> $REPO_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi

# Sync infrastructure if legacy copy exists
if [[ -d /root/hft3/infrastructure && "$REPO_DIR" != "/root/hft3" ]]; then
  rsync -a "$REPO_DIR/infrastructure/" /root/hft3/infrastructure/ 2>/dev/null || cp -a "$REPO_DIR/infrastructure/." /root/hft3/infrastructure/
fi

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
touch "$ENV_FILE"
grep -q '^HFT3_REPO_DIR=' "$ENV_FILE" && sed -i "s|^HFT3_REPO_DIR=.*|HFT3_REPO_DIR=${REPO_DIR}|" "$ENV_FILE" || echo "HFT3_REPO_DIR=${REPO_DIR}" >> "$ENV_FILE"

echo "Repo ready at $REPO_DIR ($(git -C "$REPO_DIR" rev-parse --short HEAD))"
