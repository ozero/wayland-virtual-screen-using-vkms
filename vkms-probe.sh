#!/bin/bash
# VKMS EDID 切り分け 最終確認スクリプト（要 root）
# 変更するのは debugfs の edid_override のみ。最後に reset して元に戻す。

echo "########## 1. vkms configfs の connector 属性に edid があるか ##########"
if mkdir /sys/kernel/config/vkms/probe0 2>/dev/null; then
  ls -l /sys/kernel/config/vkms/probe0/
  if mkdir /sys/kernel/config/vkms/probe0/connectors/conn0 2>/dev/null; then
    echo "--- connectors/conn0 の属性 ---"
    ls -l /sys/kernel/config/vkms/probe0/connectors/conn0/
    rmdir /sys/kernel/config/vkms/probe0/connectors/conn0
  else
    echo "[NG] connector 作成不可"
  fi
  rmdir /sys/kernel/config/vkms/probe0 2>/dev/null
else
  echo "[NG] configfs で vkms デバイスを作成できない"
fi

echo
echo "########## 2. debugfs: Virtual-1 の override 系ファイル ##########"
ls -l /sys/kernel/debug/dri/0/Virtual-1/ 2>&1

echo
echo "########## 3. 決定打テスト: edid_override に DP-1 の EDID を注入 ##########"
OV=/sys/kernel/debug/dri/0/Virtual-1/edid_override
echo "--- 注入前 ---"
echo "edid bytes = $(wc -c < /sys/class/drm/card0-Virtual-1/edid)"
echo "3840x2160 あり? = $(grep -c '^3840x2160$' /sys/class/drm/card0-Virtual-1/modes)"

if [ -e "$OV" ]; then
  cat /sys/class/drm/card1-DP-1/edid > "$OV" && echo "[OK] edid_override 書き込み成功" || echo "[NG] 書き込み失敗"
  # 再プローブを強制
  echo detect > /sys/kernel/debug/dri/0/Virtual-1/force 2>/dev/null
  cat /sys/class/drm/card0-Virtual-1/status > /dev/null
  sleep 1
  echo "--- 注入後 ---"
  echo "edid bytes = $(wc -c < /sys/class/drm/card0-Virtual-1/edid)"
  echo "3840x2160 あり? = $(grep -c '^3840x2160$' /sys/class/drm/card0-Virtual-1/modes)"
  echo "--- 後始末: override を reset ---"
  printf 'reset' > "$OV" 2>/dev/null && echo "[OK] reset" || echo "[warn] reset 失敗（再起動で消えます）"
else
  echo "[NG] edid_override が存在しない"
fi

echo
echo "########## 4. dmesg 末尾（EDID 関連） ##########"
dmesg | grep -iE "edid|vkms" | tail -20
echo "########## 完了 ##########"
