#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-time server preparation for SkillUp on a fresh Ubuntu 24.04 droplet.
# Run as root:  bash server_setup.sh
# Idempotent: safe to run again.
# ---------------------------------------------------------------------------
set -euo pipefail

APP_DIR=/var/www/skillup
APP_USER=skillup

echo "==> App user"
if ! id "$APP_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$APP_USER"
fi
usermod -aG sudo,www-data "$APP_USER"

echo "==> SSH access for $APP_USER"
install -d -m 700 -o "$APP_USER" -g "$APP_USER" "/home/$APP_USER/.ssh"
if [ -f /root/.ssh/authorized_keys ]; then
  install -m 600 -o "$APP_USER" -g "$APP_USER" \
    /root/.ssh/authorized_keys "/home/$APP_USER/.ssh/authorized_keys"
fi

echo "==> 2 GB swap (this droplet has 512 MB RAM)"
if ! swapon --show | grep -q '/swapfile'; then
  if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

echo "==> App directory"
install -d -m 755 -o "$APP_USER" -g www-data "$APP_DIR"

echo "==> Passwordless sudo for service control only"
cat >/etc/sudoers.d/skillup-deploy <<'EOF'
skillup ALL=(ALL) NOPASSWD: /usr/bin/systemctl start skillup, /usr/bin/systemctl stop skillup, /usr/bin/systemctl restart skillup, /usr/bin/systemctl status skillup, /usr/bin/journalctl -u skillup *
EOF
chmod 440 /etc/sudoers.d/skillup-deploy
visudo -c >/dev/null

echo "==> Firewall"
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null
ufw status | sed -n '1,8p'

echo "==> Summary"
free -m | head -2
df -h / | tail -1
id "$APP_USER"
echo SETUP_OK
