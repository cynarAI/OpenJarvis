#!/usr/bin/env bash
# openjarvis deploy-local — pulls latest from origin/main, syncs deps if
# needed, restarts the openjarvis.service. Idempotent: exits early if HEAD
# already matches origin. Mirrors /srv/apex/scripts/deploy-local.sh's
# fetch/dirty-check/conditional-restart structure; deliberately no new
# sophistication beyond what that already-working script does.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="${OPENJARVIS_DEPLOY_LOG:-/tmp/openjarvis-deploy.log}"
PORT="${OPENJARVIS_PORT:-8090}"

export PATH="/home/cynar/.local/bin:/usr/local/bin:/usr/bin:/bin"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }

cd "$REPO"

git fetch --quiet origin main || { log "fetch failed"; exit 1; }
LOCAL_SHA=$(git rev-parse HEAD)
REMOTE_SHA=$(git rev-parse origin/main)

if [[ "$LOCAL_SHA" == "$REMOTE_SHA" ]]; then
  exit 0
fi

log "deploying $LOCAL_SHA -> $REMOTE_SHA"

if ! git diff --quiet || ! git diff --cached --quiet; then
  log "ABORT: working tree dirty, refusing to deploy"
  exit 2
fi
git pull --ff-only --quiet origin main || { log "pull failed"; exit 1; }

if git diff --name-only "$LOCAL_SHA" "$REMOTE_SHA" | grep -qE '^(pyproject\.toml|uv\.lock)$'; then
  log "dependency files changed, running uv sync"
  uv sync --extra server >> "$LOG" 2>&1 || { log "uv sync failed"; exit 1; }
fi

log "restarting openjarvis.service"
sudo systemctl restart openjarvis.service
sleep 3

if [[ -r /etc/openjarvis/env ]]; then
  # shellcheck disable=SC1091
  API_KEY=$(grep -oP '(?<=^OPENJARVIS_API_KEY=).*' /etc/openjarvis/env || true)
fi
if [[ -n "${API_KEY:-}" ]] && curl -sf --max-time 5 -H "Authorization: Bearer $API_KEY" \
    "http://127.0.0.1:$PORT/v1/route/status" >/dev/null; then
  log "deploy OK — service healthy"
else
  log "WARN: service not responding on /v1/route/status after restart"
fi
