#!/usr/bin/env python3
"""DP-1 と Virtual-1 を 3840x2160 でミラーする構成を Mutter に適用する。

GNOME設定アプリの「ミラー」はリフレッシュレート完全一致のモードしか候補に出さず、
Virtual-1(59.993) と DP-1(59.997) は一致しないため UI からは選べない。
Mutter 本体は論理モニタ内の解像度一致のみ検証しリフレッシュは個別を許容するので、
D-Bus から直接適用する。

  python3 mirror-apply.py               # ミラー構成を適用(永続)
  python3 mirror-apply.py --virtual-only # Virtual-1 単独 4K を適用(DP-1 消灯中に実行)
  python3 mirror-apply.py --keep-hdmi    # HDMI-1 を (3840,0) の拡張として残す
  python3 mirror-apply.py --verify       # 検証のみ(適用しない)
  python3 mirror-apply.py --show         # 現在の構成を表示するだけ

--virtual-only について:
  DP-1 の電源を切ると DP リンクが落ちて connector が disconnected になり、
  Mutter から見えるモニタの集合が {DP-1, Virtual-1} -> {Virtual-1} に変わる。
  monitors.xml はモニタの組み合わせをキーに構成を選ぶため、{Virtual-1} 単独用の
  構成が無いとフォールバックして preferred モード = 1024x768 に落ちる
  (video= で足した 4K モードは USERDEF であって PREFERRED ではないため)。
  DP-1 を消した状態でこれを一度実行して {Virtual-1} 単独の構成を保存しておけば、
  以後は DP-1 消灯時も自動で 3840x2160 が復元される。
"""
import sys
from gi.repository import Gio, GLib

DEST = "org.gnome.Mutter.DisplayConfig"
PATH = "/org/gnome/Mutter/DisplayConfig"
IFACE = "org.gnome.Mutter.DisplayConfig"
W, H = 3840, 2160

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

def get_state():
    r = bus.call_sync(DEST, PATH, IFACE, "GetCurrentState", None, None,
                      Gio.DBusCallFlags.NONE, -1, None)
    return r.unpack()

def show(logical):
    for x, y, scale, transform, primary, mons, lprops in logical:
        names = ", ".join(m[0] for m in mons)
        tag = " [PRIMARY]" if primary else ""
        tag += "  ← ミラー" if len(mons) > 1 else ""
        print(f"  ({x},{y}) scale={scale}{tag}\n      {names}")

def pick_mode(monitors, conn, prefer_current=True):
    """conn の 3840x2160 モードIDを1つ返す。現在のモードを優先。"""
    for (c, *_), modes, _ in monitors:
        if c != conn:
            continue
        cands = [(mid, mp.get("is-current", False))
                 for (mid, w, h, rr, ps, ss, mp) in modes if (w, h) == (W, H)]
        if not cands:
            return None
        if prefer_current:
            for mid, cur in cands:
                if cur:
                    return mid
        return cands[0][0]
    return None

def main():
    verify_only  = "--verify" in sys.argv
    keep_hdmi    = "--keep-hdmi" in sys.argv
    virtual_only = "--virtual-only" in sys.argv

    serial, monitors, logical, props = get_state()
    conns = [m[0][0] for m in monitors]

    print("=== 適用前の構成 ===")
    show(logical)
    if "--show" in sys.argv:
        print("\n=== 接続中のモニタ ===")
        print(f"  {conns}")
        return 0

    virt = pick_mode(monitors, "Virtual-1")
    if not virt:
        print("\n[ERROR] Virtual-1 に 3840x2160 モードがありません。")
        print("        vkms-verify.sh で video=Virtual-1:3840x2160@60 を確認してください。")
        return 1

    if virtual_only or "DP-1" not in conns:
        if not virtual_only:
            print("\n[注意] DP-1 が接続されていないため Virtual-1 単独構成を適用します。")
        lm = [(0, 0, 1.0, 0, True, [("Virtual-1", virt, {})])]
        desc = f"  (0,0) [PRIMARY]\n      Virtual-1 {virt}   ← 単独"
    else:
        dp = pick_mode(monitors, "DP-1")
        if not dp:
            print("\n[ERROR] DP-1 に 3840x2160 モードがありません")
            return 1
        lm = [(0, 0, 1.0, 0, True, [("DP-1", dp, {}), ("Virtual-1", virt, {})])]
        desc = f"  (0,0) [PRIMARY]  ← ミラー\n      DP-1 {dp}, Virtual-1 {virt}"
        if keep_hdmi and "HDMI-1" in conns:
            hdmi = pick_mode(monitors, "HDMI-1")
            if hdmi:
                lm.append((W, 0, 1.0, 0, False, [("HDMI-1", hdmi, {})]))
                desc += f"\n  ({W},0)\n      HDMI-1 {hdmi}"
        elif "HDMI-1" in conns:
            desc += "\n  HDMI-1 → 無効"

    method = 0 if verify_only else 2   # 0=VERIFY 2=PERSISTENT
    print(f"\n=== 適用する構成 (method={method}) ===")
    print(desc)

    args = GLib.Variant("(uua(iiduba(ssa{sv}))a{sv})", (serial, method, lm, {}))
    try:
        bus.call_sync(DEST, PATH, IFACE, "ApplyMonitorsConfig", args, None,
                      Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as e:
        print(f"\n[FAILED] {e.message.strip()}")
        return 1

    if verify_only:
        print("\n[OK] 検証のみ成功（適用していません）")
        return 0

    print("\n[OK] 適用しました")
    serial, monitors, logical, props = get_state()
    print("\n=== 適用後の構成 ===")
    show(logical)
    return 0

sys.exit(main())
