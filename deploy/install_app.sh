#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Install/refresh the SkillUp Python environment and database on the droplet.
# Run as root:  bash install_app.sh
# ---------------------------------------------------------------------------
set -euo pipefail

APP_DIR=/var/www/skillup
cd "$APP_DIR"

echo "==> Virtual environment"
if [ ! -x venv/bin/python ]; then
  python3 -m venv venv
fi
venv/bin/pip install -q --disable-pip-version-check --upgrade pip

echo "==> Installing requirements (this takes a few minutes on 512 MB)"
venv/bin/pip install --disable-pip-version-check -r requirements.txt 2>&1 | tail -n 4

echo "==> Loading .env"
set -a
# shellcheck disable=SC1091
. "$APP_DIR/.env"
set +a
echo "    DEBUG=$DJANGO_DEBUG  ALLOWED_HOSTS=$DJANGO_ALLOWED_HOSTS"

echo "==> Migrations"
venv/bin/python manage.py migrate --no-input 2>&1 | tail -n 6

echo "==> Collecting static files"
venv/bin/python manage.py collectstatic --no-input 2>&1 | tail -n 3

echo "==> Ownership"
chown -R skillup:www-data "$APP_DIR"
chmod 600 "$APP_DIR/.env"
chmod -R g+w "$APP_DIR/media" "$APP_DIR/db.sqlite3"

echo "==> Versions"
venv/bin/python - <<'PY'
import django, gunicorn, whitenoise, lxml, sys
print(f"    python   {sys.version.split()[0]}")
print(f"    django   {django.get_version()}")
print(f"    gunicorn {gunicorn.__version__}")
PY
echo INSTALL_OK
