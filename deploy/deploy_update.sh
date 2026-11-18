#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# SkillUp server-side updater  (runs ON the droplet, not on Windows)
#
#   bash deploy/deploy_update.sh [archive]
#   archive defaults to ~/skillup_deploy.tar.gz
#
# Run as the `skillup` user (it only needs sudo for systemctl). It:
#   1. backs up db.sqlite3 + .env
#   2. stops the service
#   3. unpacks the archive over the app directory
#   4. installs requirements, migrates, collects static
#   5. restarts and health-checks, rolling back the database on a bad start
# ---------------------------------------------------------------------------
set -euo pipefail

APP_DIR="${APP_DIR:-/var/www/skillup}"
SERVICE="${SERVICE:-skillup}"
ARCHIVE="${1:-$HOME/skillup_deploy.tar.gz}"
KEEP_BACKUPS="${KEEP_BACKUPS:-10}"

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$APP_DIR/backups/$STAMP"

if [ ! -f "$ARCHIVE" ]; then
  echo "ERROR: archive not found: $ARCHIVE" >&2
  exit 1
fi

# The script restarts the service through passwordless sudo (see sudoers rule
# in the guide). Fail fast instead of hanging on a password prompt.
if ! sudo -n -l /usr/bin/systemctl restart "$SERVICE" >/dev/null 2>&1; then
  echo "ERROR: this script needs passwordless sudo for systemctl." >&2
  echo "Add /etc/sudoers.d/skillup-deploy as shown in the guide, Part 9." >&2
  exit 1
fi

if [ "$(id -u)" -eq 0 ]; then
  echo "ERROR: run as the 'skillup' user, not root — otherwise new files end up root-owned." >&2
  exit 1
fi

cd "$APP_DIR"

echo "==> Backing up database and env to $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
if [ -f db.sqlite3 ]; then
  # .backup is the only safe way to copy a live SQLite file
  "$APP_DIR/venv/bin/python" - "$BACKUP_DIR" <<'PY'
import sqlite3, sys, pathlib
dest = pathlib.Path(sys.argv[1]) / "db.sqlite3"
src = sqlite3.connect("db.sqlite3")
dst = sqlite3.connect(str(dest))
with dst:
    src.backup(dst)
for c in (src, dst):
    c.close()
print(f"    database copied to {dest}")
PY
fi
if [ -f .env ]; then
  cp .env "$BACKUP_DIR/.env"
fi

echo "==> Stopping $SERVICE"
sudo systemctl stop "$SERVICE" 2>/dev/null || true

echo "==> Extracting $ARCHIVE"
tar -xzf "$ARCHIVE" -C "$APP_DIR"

# Archives built on Windows carry 666/777 modes; normalise them so the app is
# not world-writable (and skip the venv, which pip owns).
echo "==> Normalising permissions"
find "$APP_DIR" -path "$APP_DIR/venv" -prune -o -path "$APP_DIR/backups" -prune -o -type d -exec chmod 755 {} +
find "$APP_DIR" -path "$APP_DIR/venv" -prune -o -path "$APP_DIR/backups" -prune -o -type f -exec chmod 644 {} +
chmod 600 "$APP_DIR/.env" 2>/dev/null || true

echo "==> Loading environment"
set -a
# shellcheck disable=SC1091
source "$APP_DIR/.env"
set +a

echo "==> Installing requirements"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r requirements.txt -q

echo "==> Applying migrations"
"$APP_DIR/venv/bin/python" manage.py migrate --no-input

echo "==> Collecting static files"
"$APP_DIR/venv/bin/python" manage.py collectstatic --no-input

echo "==> Starting $SERVICE"
sudo systemctl start "$SERVICE"

echo "==> Health check"
# The check must send a host Django allows, otherwise a strict ALLOWED_HOSTS
# answers 400 and looks like a failed deploy.
HEALTH_HOST="${HEALTH_HOST:-$(printf '%s' "${DJANGO_ALLOWED_HOSTS:-127.0.0.1}" | cut -d, -f1 | tr -d ' ')}"
[ -n "$HEALTH_HOST" ] || HEALTH_HOST=127.0.0.1
echo "    probing Host: $HEALTH_HOST"
ok=""
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 2
  code="$(curl -s -o /dev/null -w '%{http_code}' --unix-socket /run/skillup/gunicorn.sock -H "Host: $HEALTH_HOST" http://localhost/ || true)"
  case "$code" in
    2*|3*) ok="yes"; break ;;
  esac
done

if [ -z "$ok" ]; then
  echo "!! App did not answer after restart (last status: ${code:-none})" >&2
  echo "!! Restoring database from $BACKUP_DIR" >&2
  if [ -f "$BACKUP_DIR/db.sqlite3" ]; then
    sudo systemctl stop "$SERVICE" 2>/dev/null || true
    cp "$BACKUP_DIR/db.sqlite3" "$APP_DIR/db.sqlite3"
    sudo systemctl start "$SERVICE" 2>/dev/null || true
  fi
  echo "---- last service log lines ----" >&2
  sudo journalctl -u "$SERVICE" -n 40 --no-pager >&2 || true
  exit 1
fi

echo "==> Pruning old backups (keeping $KEEP_BACKUPS)"
ls -1dt "$APP_DIR"/backups/*/ 2>/dev/null | tail -n "+$((KEEP_BACKUPS + 1))" | xargs -r rm -rf

echo "==> Done. Live at http://$(hostname -I | awk '{print $1}')/ (status $code)"
