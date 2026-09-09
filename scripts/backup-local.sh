#!/usr/bin/env bash
# openjarvis backup-local — periodic safety net for the self-improvement loop.
# Snapshots runtime state (config.toml + SQLite DBs under ~/.openjarvis, taken
# via the SQLite online backup API so it's safe while jarvis-serve.service is
# writing) plus a full git bundle of the repo (all refs — a rollback point
# independent of GitHub). Mirrors /srv/trading-bot/trading-bot-backup.sh's
# pattern. Keeps 7 days of backups.
set -euo pipefail

SRC="${OPENJARVIS_CONFIG_DIR:-$HOME/.openjarvis}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${OPENJARVIS_BACKUP_DIR:-/srv/backups/openjarvis}"

mkdir -p "$DEST" && chmod 700 "$DEST"
stamp=$(date -u +%Y-%m-%dT%H-%M-%SZ)
work="$DEST/.tmp-$stamp"
mkdir -p "$work"

for db in "$SRC"/*.db; do
  [[ -f "$db" ]] || continue
  name="$(basename "$db")"
  sqlite3 "$db" ".backup '$work/$name'"
done

# Non-DB config/state worth keeping (contains the server API key — restrict perms).
cp "$SRC/config.toml" "$work/" 2>/dev/null || true
cp "$SRC/anon_id" "$work/" 2>/dev/null || true

tar -czf "$DEST/state-$stamp.tgz" -C "$work" .
rm -rf "$work"
chmod 600 "$DEST/state-$stamp.tgz"

git -C "$REPO" bundle create "$DEST/repo-$stamp.bundle" --all >/dev/null 2>&1 \
  || echo "[$(date '+%F %T')] WARN: git bundle failed" >> "$DEST/backup.log"
chmod 600 "$DEST/repo-$stamp.bundle" 2>/dev/null || true

find "$DEST" -maxdepth 1 -type f \( -name 'state-*.tgz' -o -name 'repo-*.bundle' \) -mtime +7 -delete

echo "[$(date '+%F %T')] backup ok: $(du -sh "$DEST" | cut -f1) total, newest state-$stamp.tgz" >> "$DEST/backup.log"
