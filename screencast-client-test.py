#!/usr/bin/env python3
"""ScreenCast ポータルをフロントエンド側から叩く E2E テスト。

RustDesk を使わずに「ダイアログが出ずに PipeWire の node_id が返るか」を確かめる。

  python3 screencast-client-test.py            # 一連の流れを実行して node_id を表示
  python3 screencast-client-test.py --cursor 4 # cursor_mode を指定 (1=hidden 2=embedded 4=metadata)

終了コード 0 = 成功、1 = 失敗、2 = ポータルが応答しない。
"""
import os
import sys

from gi.repository import Gio, GLib

BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"

SOURCE_TYPE_MONITOR = 1


class Client:
    def __init__(self, bus):
        self.bus = bus
        self.loop = GLib.MainLoop()
        self.pending = {}   # request path -> callback
        self.early = {}     # request path -> (response, results)
        self.failure = None
        self.token = 0
        # パスを問わず Response を購読する。メソッドの戻り値より先に届くことがあるため。
        self.bus.signal_subscribe(
            BUS, REQUEST_IFACE, "Response", None, None,
            Gio.DBusSignalFlags.NONE, self._on_response, None)

    def _on_response(self, _conn, _sender, path, _iface, _signal, params):
        response, results = params.unpack()
        callback = self.pending.pop(path, None)
        if callback is None:
            self.early[path] = (response, results)
            return
        callback(response, results)

    def _next_token(self):
        self.token += 1
        return "clienttest%d" % self.token

    def call(self, method, args, options, on_result):
        """ポータルのメソッドを呼び、Response シグナルの結果を on_result に渡す。"""
        options = dict(options)
        options["handle_token"] = GLib.Variant("s", self._next_token())
        params = GLib.Variant.new_tuple(*(list(args) + [GLib.Variant("a{sv}", options)]))

        def on_done(_source, res):
            try:
                path = self.bus.call_finish(res).unpack()[0]
            except GLib.Error as err:
                self.fail("%s の呼び出しに失敗: %s" % (method, err.message))
                return
            if path in self.early:
                response, results = self.early.pop(path)
                on_result(response, results)
                return
            self.pending[path] = on_result

        self.bus.call(BUS, PATH, IFACE, method, params,
                      GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE,
                      -1, None, on_done)

    def fail(self, message):
        self.failure = message
        self.loop.quit()

    def close_session(self, session):
        """作ったセッションを明示的に閉じる。

        プロセスが終われば xdg-desktop-portal が片付けるが、それは非同期なので
        連続実行したときに前回のセッションを観測しうる。判定装置として
        決定的にするためここで閉じる。二重に呼んでも害はない。
        """
        if session is None:
            return
        try:
            self.bus.call_sync(BUS, session, "org.freedesktop.portal.Session",
                               "Close", None, None, Gio.DBusCallFlags.NONE,
                               5000, None)
        except GLib.Error:
            pass   # 既に閉じている / 相手が消えている場合は何もしなくてよい


def main(argv):
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0

    cursor_mode = 2
    if "--cursor" in argv:
        i = argv.index("--cursor")
        if i + 1 >= len(argv):
            print("--cursor には値が必要です (1=hidden 2=embedded 4=metadata)",
                  file=sys.stderr)
            return 1
        try:
            cursor_mode = int(argv[i + 1])
        except ValueError:
            print("--cursor の値が数値ではありません: %r" % argv[i + 1],
                  file=sys.stderr)
            return 1
        if cursor_mode not in (1, 2, 4):
            print("--cursor は 1(hidden) / 2(embedded) / 4(metadata) のいずれか",
                  file=sys.stderr)
            return 1

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    client = Client(bus)
    state = {"session": None, "node_id": None}

    def on_started(response, results):
        if response != 0:
            client.fail("Start が response=%d を返した" % response)
            return
        streams = results.get("streams") or []
        if not streams:
            client.fail("streams が空")
            return
        node_id, props = streams[0]
        state["node_id"] = node_id
        print("成功: node_id=%d position=%s size=%s source_type=%s"
              % (node_id, props.get("position"), props.get("size"),
                 props.get("source_type")))
        client.loop.quit()

    def on_sources_selected(response, _results):
        if response != 0:
            client.fail("SelectSources が response=%d を返した" % response)
            return
        client.call("Start", [GLib.Variant("o", state["session"]),
                              GLib.Variant("s", "")], {}, on_started)

    def on_session_created(response, results):
        if response != 0:
            client.fail("CreateSession が response=%d を返した" % response)
            return
        state["session"] = results["session_handle"]
        print("session=%s" % state["session"])
        client.call("SelectSources", [GLib.Variant("o", state["session"])], {
            "types": GLib.Variant("u", SOURCE_TYPE_MONITOR),
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", cursor_mode),
        }, on_sources_selected)

    client.call("CreateSession", [], {
        "session_handle_token": GLib.Variant("s", "clienttest%d" % os.getpid()),
    }, on_session_created)

    GLib.timeout_add_seconds(30, lambda: (client.fail("30秒で応答なし"), False)[1])
    client.loop.run()
    client.close_session(state.get("session"))

    if client.failure:
        print("失敗: %s" % client.failure, file=sys.stderr)
        return 2 if "応答なし" in client.failure else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
