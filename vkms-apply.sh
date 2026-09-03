#!/bin/bash
set -euo pipefail
# VKMS Virtual-1 に 3840x2160 を注入する恒久設定（要 root / 再起動が必要）
MODE="video=Virtual-1:3840x2160@60"

echo "########## 1. /etc/default/grub をバックアップ ##########"
cp -a /etc/default/grub /etc/default/grub.bak.$(date +%Y%m%d-%H%M%S)
ls -1t /etc/default/grub.bak.* | head -1

echo
echo "########## 2. video= を追加（冪等） ##########"
if grep -q "video=Virtual-1:" /etc/default/grub; then
  echo "[skip] video=Virtual-1: は既に設定済み"
else
  sed -i "s|^\(GRUB_CMDLINE_LINUX_DEFAULT=\"[^\"]*\)\"|\1 ${MODE}\"|" /etc/default/grub
  echo "[OK] 追加しました"
fi
# 効かないと実証済みの drm.edid_firmware が混入していたら除去
sed -i 's| drm\.edid_firmware=[^ "]*||g' /etc/default/grub
echo "--- 現在の設定 ---"
grep -E "^GRUB_CMDLINE_LINUX_DEFAULT" /etc/default/grub

echo
echo "########## 3. vkms を起動時に自動ロード（現状は再起動で消える） ##########"
cat > /etc/modprobe.d/vkms.conf <<'EOM'
options vkms create_default_dev=1 enable_cursor=1
EOM
cat > /etc/modules-load.d/vkms.conf <<'EOM'
vkms
EOM
echo "--- /etc/modprobe.d/vkms.conf ---";    cat /etc/modprobe.d/vkms.conf
echo "--- /etc/modules-load.d/vkms.conf ---"; cat /etc/modules-load.d/vkms.conf

echo
echo "########## 4. update-grub ##########"
update-grub

echo
echo "########## 完了 ##########"
echo "再起動してください:  sudo reboot"
echo "再起動後の確認:      bash $(dirname "$0")/vkms-verify.sh"
