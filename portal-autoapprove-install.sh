#!/usr/bin/env bash
# ScreenCast ポータルの自作バックエンドを設置/撤去する。
#
#   bash portal-autoapprove-install.sh --install [--mode MODE]
#   bash portal-autoapprove-install.sh --uninstall
#   bash portal-autoapprove-install.sh --status
#
# --install は .portal の設置に sudo を1回だけ使う。それ以外はユーザ権限で完結する。
# MODE は rustdesk-connected(既定) | always | never。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME=org.freedesktop.impl.portal.desktop.autoapprove
PORTAL_DIR=/usr/share/xdg-desktop-portal/portals
DBUS_DIR="$HOME/.local/share/dbus-1/services"
UNIT_DIR="$HOME/.config/systemd/user"
CONF_DIR="$HOME/.config/xdg-desktop-portal"
CONF="$CONF_DIR/gnome-portals.conf"
MODE=rustdesk-connected

install_all() {
  sudo install -Dm644 "$REPO/install/autoapprove.portal" \
       "$PORTAL_DIR/autoapprove.portal"

  mkdir -p "$DBUS_DIR" "$UNIT_DIR" "$CONF_DIR"

  cat > "$DBUS_DIR/$NAME.service" <<EOF
[D-BUS Service]
Name=$NAME
Exec=/usr/bin/python3 $REPO/portal-autoapprove.py
SystemdService=portal-autoapprove.service
EOF

  cat > "$UNIT_DIR/portal-autoapprove.service" <<EOF
[Unit]
Description=Portal service (autoapprove ScreenCast implementation)
After=graphical-session.target
Requisite=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=dbus
BusName=$NAME
ExecStart=/usr/bin/python3 $REPO/portal-autoapprove.py --auto-approve-when $MODE
Restart=on-failure
RestartSec=1
EOF

  cat > "$CONF" <<'EOF'
[preferred]
default=gnome;gtk;
org.freedesktop.impl.portal.Secret=gnome-keyring;
org.freedesktop.impl.portal.ScreenCast=autoapprove;gnome;
EOF

  systemctl --user daemon-reload
  systemctl --user restart xdg-desktop-portal.service
  echo "設置しました。mode=$MODE"
  echo "元に戻すには: bash $0 --uninstall"
}

uninstall_all() {
  rm -f "$CONF" "$DBUS_DIR/$NAME.service" "$UNIT_DIR/portal-autoapprove.service"
  sudo rm -f "$PORTAL_DIR/autoapprove.portal"
  systemctl --user stop portal-autoapprove.service 2>/dev/null || true
  systemctl --user daemon-reload
  systemctl --user restart xdg-desktop-portal.service
  echo "撤去しました。GNOME の既定バックエンドに戻っています。"
}

status_all() {
  echo "--- portals.conf ---"; cat "$CONF" 2>/dev/null || echo "(なし)"
  echo "--- .portal ---";      ls -l "$PORTAL_DIR/autoapprove.portal" 2>/dev/null || echo "(なし)"
  echo "--- unit ---";         systemctl --user status portal-autoapprove.service --no-pager 2>&1 | head -5
}

case "${1:---status}" in
  --install)   shift; [ "${1:-}" = "--mode" ] && MODE="$2"; install_all ;;
  --uninstall) uninstall_all ;;
  --status)    status_all ;;
  *) echo "使い方: $0 --install [--mode MODE] | --uninstall | --status"; exit 1 ;;
esac
