#!/bin/bash
# 再起動後の検証（root 不要）
echo "########## 1. カーネルコマンドライン ##########"
grep -o 'video=Virtual-1:[^ ]*' /proc/cmdline || echo "[NG] video= が cmdline に無い"

echo
echo "########## 2. vkms が自動ロードされたか ##########"
grep -q '^vkms ' /proc/modules && echo "[OK] vkms ロード済み" || echo "[NG] vkms 未ロード"

echo
echo "########## 3. Virtual-1 connector ##########"
C=$(ls -d /sys/class/drm/card*-Virtual-1 2>/dev/null | head -1)
if [ -z "$C" ]; then echo "[NG] Virtual-1 が存在しない"; exit 1; fi
echo "path   = $C"
echo "status = $(cat $C/status)"

echo
echo "########## 4. ★本命: 3840x2160 がモードリストにあるか ##########"
if grep -qx '3840x2160' $C/modes; then
  echo "[SUCCESS] 3840x2160 が出現しました"
else
  echo "[FAIL] 3840x2160 なし"
fi
echo "--- modes 先頭10件 ---"
head -10 $C/modes

echo
echo "########## 5. Mutter から見た Virtual-1 のモード ##########"
gdbus call --session --dest org.gnome.Mutter.DisplayConfig \
  --object-path /org/gnome/Mutter/DisplayConfig \
  --method org.gnome.Mutter.DisplayConfig.GetCurrentState 2>/dev/null \
  | tr ',' '\n' | grep -oE "'3840x2160@[0-9.]+'" | sort -u \
  || echo "(Mutter に 3840x2160 が見えていない)"
