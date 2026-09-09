#!/usr/bin/env bash
# openjarvis deploy-local — pulls latest from origin/main, syncs deps if
# needed, restarts the *actual* running server (jarvis-serve.service, a
# systemd --user unit — NOT the system-level openjarvis.service template,
# which is unused/inactive; see CLAUDE.md "Server-Betrieb"). Idempotent:
# exits early if HEAD already matches origin.
#
# Safety net: tags the pre-deploy commit before pulling. If the post-restart
# health check fails, automatically rolls back to that commit, restarts
# again, and exits non-zero — so a bad self-edit can't stay live unnoticed.
# On a healthy deploy, the rolling tag `jarvis-last-good` is moved to HEAD
# and pushed to origin as a durable rollback point.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="${OPENJARVIS_DEPLOY_LOG:-/tmp/openjarvis-deploy.log}"
PORT="${OPENJARVIS_PORT:-8010}"
SERVICE="jarvis-serve.service"
CONFIG_TOML="${OPENJARVIS_CONFIG:-$HOME/.openjarvis/config.toml}"

export PATH="/home/cynar/.local/bin:/usr/local/bin:/usr/bin:/bin"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }

restart_and_check() {
  log "restarting $SERVICE"
  systemctl --user restart "$SERVICE"

  local api_key=""
  if [[ -r "$CONFIG_TOML" ]]; then
    api_key=$(python3 -c "
import tomllib, sys
try:
    with open(sys.argv[1], 'rb') as f:
        data = tomllib.load(f)
    print(data.get('server', {}).get('auth', {}).get('api_key', ''))
except Exception:
    pass
" "$CONFIG_TOML" 2>/dev/null || true)
  fi

  local attempt
  for attempt in 1 2 3 4 5; do
    sleep 3
    if [[ -n "$api_key" ]] && curl -sf --max-time 5 -H "Authorization: Bearer $api_key" \
        "http://127.0.0.1:$PORT/v1/route/status" >/dev/null 2>&1; then
      return 0
    fi
  done
  return 1
}

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

# Rollback point, tagged before touching the tree.
git tag -f "pre-deploy-$(date +%Y%m%d-%H%M%S)" "$LOCAL_SHA" >/dev/null

git pull --ff-only --quiet origin main || { log "pull failed"; exit 1; }

if git diff --name-only "$LOCAL_SHA" "$REMOTE_SHA" | grep -qE '^(pyproject\.toml|uv\.lock)$'; then
  log "dependency files changed, running uv sync"
  uv sync --extra server >> "$LOG" 2>&1 || { log "uv sync failed"; exit 1; }
fi

if restart_and_check; then
  log "deploy OK — service healthy at $REMOTE_SHA"
  git tag -f jarvis-last-good HEAD >/dev/null
  git push --quiet --force origin refs/tags/jarvis-last-good \
    || log "WARN: could not push jarvis-last-good tag (non-fatal)"
  exit 0
fi

log "CRITICAL: service unhealthy after deploying $REMOTE_SHA — rolling back to $LOCAL_SHA"
git reset --hard "$LOCAL_SHA" >> "$LOG" 2>&1

if restart_and_check; then
  log "rollback OK — service healthy again at $LOCAL_SHA"
else
  log "CRITICAL: service still unhealthy after rollback to $LOCAL_SHA — needs manual attention"
fi
exit 1
