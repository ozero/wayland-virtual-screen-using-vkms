#!/usr/bin/env python3
"""画面共有(ScreenCast)ポータルの自作バックエンド。

RustDesk 接続中の画面キャプチャ要求だけを UI 無しで承認し、それ以外は
xdg-desktop-portal-gnome へそのまま中継する。中継するので「承認しない要求」は
拒否ではなく従来どおりダイアログになる。

  --prefer-connector NAME    記録する connector 名 (既定 Virtual-1)
  --auto-approve-when MODE   rustdesk-connected | always | never (既定 rustdesk-connected)
  --retry-seconds N          Mutter の inhibit に対するリトライ上限 (既定 10)
  --fallback-backend NAME    中継先 (既定 gnome)
  --policy-grace-ms N        rustdesk --cm の出現を待つ猶予 (既定 1500)

停止して元の挙動に戻すには ~/.config/xdg-desktop-portal/gnome-portals.conf を消して
systemctl --user restart xdg-desktop-portal すればよい。
"""
import logging
import sys

from gi.repository import Gio, GLib

from portal_autoapprove import impl, policy

DEFAULTS = {
    "--prefer-connector": "Virtual-1",
    "--auto-approve-when": policy.MODE_RUSTDESK_CONNECTED,
    "--retry-seconds": "10",
    "--fallback-backend": "gnome",
    "--policy-grace-ms": "1500",
}


def parse_args(argv):
    opts = dict(DEFAULTS)
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(__doc__)
            raise SystemExit(0)
        if arg not in DEFAULTS:
            raise SystemExit("未知の引数: %s" % arg)
        if i + 1 >= len(argv):
            raise SystemExit("%s に値がない" % arg)
        opts[arg] = argv[i + 1]
        i += 2
    if opts["--auto-approve-when"] not in policy.MODES:
        raise SystemExit("--auto-approve-when は %s のいずれか"
                         % " | ".join(policy.MODES))
    return opts


def main(argv):
    opts = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        stream=sys.stderr)
    log = logging.getLogger("portal-autoapprove")

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    backend = impl.ScreenCastBackend(
        bus,
        "org.freedesktop.impl.portal.desktop.%s" % opts["--fallback-backend"],
        opts["--prefer-connector"],
        opts["--auto-approve-when"],
        int(opts["--retry-seconds"]),
        int(opts["--policy-grace-ms"]))

    loop = GLib.MainLoop()

    # 名前を取ってからオブジェクトを export すると、その間に届いた最初の要求が
    # 「そのインターフェースは無い」で失敗しうる(実機で確認済み。gdbus introspect が
    # 起動直後に1回だけ空を返す)。名前を取る前に export を済ませてこの窓を消す。
    backend.register()

    def on_name_lost(*_args):
        log.error("D-Bus 名 %s を取得できなかった", impl.BUS_NAME)
        loop.quit()

    Gio.bus_own_name(Gio.BusType.SESSION, impl.BUS_NAME,
                     Gio.BusNameOwnerFlags.NONE, None,
                     None, on_name_lost)
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
