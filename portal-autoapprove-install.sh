#!/usr/bin/env bash
# ScreenCast ポータルの自作バックエンドを設置/撤去する。
#
#   bash portal-autoapprove-install.sh --install [--mode MODE]
#   bash portal-autoapprove-install.sh --uninstall
#   bash portal-autoapprove-install.sh --status
#
# --install と --uninstall は /usr/share 配下の .portal の設置/撤去に sudo を1回だけ
# 使う。それ以外(--status も含む)はすべてユーザ権限で完結する。
# MODE は rustdesk-connected(既定) | always | never。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME=org.freedesktop.impl.portal.desktop.autoapprove
PORTAL_DIR=/usr/share/xdg-desktop-portal/portals
DBUS_DIR="$HOME/.local/share/dbus-1/services"
UNIT_DIR="$HOME/.config/systemd/user"
CONF_DIR="$HOME/.config/xdg-desktop-portal"
MODE=rustdesk-connected
MARKER="# managed by portal-autoapprove-install.sh"

# xdp は XDG_CURRENT_DESKTOP を ':' で分割した順に <desktop>-portals.conf を探し、
# 最後に portals.conf を見る。ユーザ設定ディレクトリが最優先。どれが読まれるかは
# 環境変数の内容に依存するので、候補名すべてに同じ内容を書いて取りこぼしを無くす。
conf_names() {
  local desktop
  IFS=':' read -ra desktops <<< "${XDG_CURRENT_DESKTOP:-}"
  for desktop in "${desktops[@]:-}"; do
    [ -n "$desktop" ] && echo "$(echo "$desktop" | tr '[:upper:]' '[:lower:]')-portals.conf"
  done
  echo "portals.conf"
}

install_all() {
  sudo install -Dm644 "$REPO/install/autoapprove.portal" \
       "$PORTAL_DIR/autoapprove.portal"

  mkdir -p "$DBUS_DIR" "$UNIT_DIR" "$CONF_DIR"

  cat > "$DBUS_DIR/$NAME.service" <<EOF
[D-BUS Service]
Name=$NAME
Exec=/usr/bin/python3 $REPO/portal-autoapprove.py --auto-approve-when $MODE
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

  for name in $(conf_names); do
    f="$CONF_DIR/$name"
    if [ -f "$f" ] && ! head -1 "$f" | grep -qF "$MARKER"; then
      cp -a "$f" "$f.bak.$(date +%Y%m%d%H%M%S)"
      echo "既存の $f を退避しました: $f.bak.*"
    fi
    cat > "$f" <<EOF
$MARKER
[preferred]
default=gnome;gtk;
org.freedesktop.impl.portal.Secret=gnome-keyring;
org.freedesktop.impl.portal.ScreenCast=autoapprove;
EOF
  done

  systemctl --user daemon-reload
  systemctl --user restart xdg-desktop-portal.service
  echo "設置しました。mode=$MODE"
  echo "設定ファイル: $(conf_names | head -1) が最初に読まれます"
  echo "元に戻すには: bash $0 --uninstall"
}

uninstall_all() {
  for name in $(conf_names) gnome-portals.conf ubuntu-portals.conf; do
    f="$CONF_DIR/$name"
    if [ -f "$f" ] && head -1 "$f" | grep -qF "$MARKER"; then
      rm -f "$f"
      echo "削除: $f"
    fi
  done
  rm -f "$DBUS_DIR/$NAME.service" "$UNIT_DIR/portal-autoapprove.service"
  sudo rm -f "$PORTAL_DIR/autoapprove.portal"
  systemctl --user stop portal-autoapprove.service 2>/dev/null || true
  systemctl --user daemon-reload
  systemctl --user restart xdg-desktop-portal.service
  echo "撤去しました。GNOME の既定バックエンドに戻っています。"
}

status_all() {
  echo "--- portals.conf の候補 ---"
  for name in $(conf_names) gnome-portals.conf ubuntu-portals.conf; do
    f="$CONF_DIR/$name"
    [ -f "$f" ] && echo "  [ある] $f" || echo "  [ない] $f"
  done
  echo "--- ScreenCast の振り先 ---"
  grep -h "impl.portal.ScreenCast" "$CONF_DIR"/*portals.conf 2>/dev/null | sort -u | sed 's/^/  /' \
    || echo "  (設定なし)"
  echo "--- .portal ---"; ls -l "$PORTAL_DIR/autoapprove.portal" 2>/dev/null || echo "  (なし)"
  echo "--- unit ---";     systemctl --user status portal-autoapprove.service --no-pager 2>&1 | head -5
}

case "${1:---status}" in
  --install)   shift; [ "${1:-}" = "--mode" ] && MODE="$2"; install_all ;;
  --uninstall) uninstall_all ;;
  --status)    status_all ;;
  *) echo "使い方: $0 --install [--mode MODE] | --uninstall | --status"; exit 1 ;;
esac
