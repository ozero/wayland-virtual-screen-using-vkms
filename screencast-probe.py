#!/usr/bin/env python3
"""Mutter の ScreenCast にセッションを作れるかを直接確かめる探針。

「画面を共有」ダイアログを自動承認しても、Mutter が
  Session creation inhibited
を返す状態では結局セッションが作れない。ポータル層に手を入れる前に、
まず Mutter 単体で通るかどうかをここで確かめる。

  python3 screencast-probe.py                       # Virtual-1 を記録して即停止
  python3 screencast-probe.py --connector DP-1      # 対象を変える
  python3 screencast-probe.py --keep                # Ctrl-C まで記録し続ける
  python3 screencast-probe.py --show                # モニタ構成だけ表示

副作用は「画面共有中」インジケータが数秒出るだけ。--keep でなければ自動で止まる。
"""
import sys

from gi.repository import Gio, GLib

from portal_autoapprove import monitors, mutter, protocol


def show(state):
    print("connectors:", ", ".join(monitors.connectors(state)))
    for name in monitors.connectors(state):
        pos, size = monitors.stream_geometry(state, name)
        print("  %-12s position=%s size=%dx%d" % (name, pos, size[0], size[1]))


def main(argv):
    connector = None
    keep = False
    for i, arg in enumerate(argv):
        if arg == "--connector":
            connector = argv[i + 1]
        elif arg == "--keep":
            keep = True
        elif arg in ("-h", "--help"):
            print(__doc__)
            return 0

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    state = mutter.get_current_state(bus)

    if "--show" in argv:
        show(state)
        return 0

    if connector is None:
        connector = monitors.select_connector(state, "Virtual-1")
    pos, size = monitors.stream_geometry(state, connector)
    print("記録対象: %s position=%s size=%dx%d" % (connector, pos, size[0], size[1]))

    loop = GLib.MainLoop()
    status = {"code": 1}

    def on_ready(recording):
        print("成功: node_id=%d" % recording.node_id)
        print("→ Mutter 側は健全。ポータル層の対策(Phase 1)が成立する。")
        status["code"] = 0
        if keep:
            print("--keep 指定。Ctrl-C で停止する。")
            return
        recording.stop()
        loop.quit()

    def on_error(exc):
        if isinstance(exc, mutter.InhibitedError):
            print("失敗: セッション作成が inhibit されている")
            print("  %s" % exc)
            print("→ この状態ではポータル層に手を入れても解決しない。")
            print("  gnome-shell 側の inhibit 要因を先に特定すること。")
            status["code"] = 2
        else:
            print("失敗: %s" % exc)
            status["code"] = 1
        loop.quit()

    mutter.record_monitor(bus, connector, protocol.CURSOR_MODE_EMBEDDED,
                          on_ready, on_error)
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\n中断")
    return status["code"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
