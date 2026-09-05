"""org.gnome.Mutter.ScreenCast の非同期ラッパ。

同期呼び出しで書くとデッドロックする。CreateSession -> RecordMonitor -> Start の
往復の途中で PipeWireStreamAdded シグナルを同じメインループで受け取る必要があるため。
すべて bus.call() の非同期版で組んである。
"""
from gi.repository import Gio, GLib

from portal_autoapprove import protocol

SCREEN_CAST_NAME = "org.gnome.Mutter.ScreenCast"
SCREEN_CAST_PATH = "/org/gnome/Mutter/ScreenCast"
SCREEN_CAST_IFACE = "org.gnome.Mutter.ScreenCast"
SESSION_IFACE = "org.gnome.Mutter.ScreenCast.Session"
STREAM_IFACE = "org.gnome.Mutter.ScreenCast.Stream"

DISPLAY_CONFIG_NAME = "org.gnome.Mutter.DisplayConfig"
DISPLAY_CONFIG_PATH = "/org/gnome/Mutter/DisplayConfig"
DISPLAY_CONFIG_IFACE = "org.gnome.Mutter.DisplayConfig"

# Mutter が gnome-shell 側の inhibit で断ってくるときの文字列。
# libmutter-14.so.0 の "Session creation inhibited" に対応する。
_INHIBITED_MARKER = "Session creation inhibited"


class ScreenCastError(Exception):
    """Mutter の ScreenCast 操作の失敗。"""


class InhibitedError(ScreenCastError):
    """gnome-shell 側が remote access を inhibit しているためセッションを作れない。"""


def _wrap_error(error):
    if error is not None and _INHIBITED_MARKER in error.message:
        return InhibitedError(error.message)
    return ScreenCastError(error.message if error else "不明なエラー")


def get_current_state(bus):
    """DisplayConfig.GetCurrentState の結果を unpack して返す。

    monitors.py に渡すための同期呼び出し。シグナル待ちを含まないので安全。
    """
    result = bus.call_sync(
        DISPLAY_CONFIG_NAME, DISPLAY_CONFIG_PATH, DISPLAY_CONFIG_IFACE,
        "GetCurrentState", None, None, Gio.DBusCallFlags.NONE, -1, None)
    return result.unpack()


class Recording:
    """Mutter の ScreenCast セッション1本と、その中の1ストリーム。"""

    def __init__(self, bus, session_path, stream_path, node_id):
        self._bus = bus
        self._session_path = session_path
        self._stream_path = stream_path
        self._closed_subscription = None
        self.node_id = node_id

    def connect_closed(self, callback):
        """Mutter 側でセッションが閉じられたときに callback() を呼ぶ。"""
        self._closed_subscription = self._bus.signal_subscribe(
            SCREEN_CAST_NAME, SESSION_IFACE, "Closed", self._session_path, None,
            Gio.DBusSignalFlags.NONE,
            lambda *_args: callback(), None)

    def stop(self):
        """セッションを閉じる。二重呼び出しは無害。"""
        if self._closed_subscription is not None:
            self._bus.signal_unsubscribe(self._closed_subscription)
            self._closed_subscription = None
        if self._session_path is None:
            return
        path, self._session_path = self._session_path, None
        self._bus.call(
            SCREEN_CAST_NAME, path, SESSION_IFACE, "Stop", None, None,
            Gio.DBusCallFlags.NONE, -1, None, None, None)


def record_monitor(bus, connector, cursor_mode, on_ready, on_error, timeout_ms=5000):
    """指定 connector の記録を開始し、node_id が取れたら on_ready(Recording) を呼ぶ。

    cursor_mode はポータル側の値(1/2/4)を渡すこと。内部で Mutter の値に変換する。
    失敗時は on_error(Exception) を呼ぶ。inhibit なら InhibitedError が渡る。
    GLib のメインループが回っていることが前提。
    """
    state = {"done": False, "session_path": None, "stream_path": None,
             "subscription": None, "timeout_id": None}

    def finish_error(exc):
        if state["done"]:
            return
        state["done"] = True
        _cleanup(bus, state)
        on_error(exc)

    def finish_ok(node_id):
        if state["done"]:
            return
        state["done"] = True
        if state["timeout_id"] is not None:
            GLib.source_remove(state["timeout_id"])
            state["timeout_id"] = None
        if state["subscription"] is not None:
            bus.signal_unsubscribe(state["subscription"])
            state["subscription"] = None
        on_ready(Recording(bus, state["session_path"], state["stream_path"], node_id))

    def on_timeout():
        state["timeout_id"] = None
        finish_error(ScreenCastError(
            "PipeWireStreamAdded が %dms 以内に来なかった" % timeout_ms))
        return GLib.SOURCE_REMOVE

    def on_stream_added(_conn, _sender, _path, _iface, _signal, params):
        finish_ok(params.unpack()[0])

    def on_start_done(_source, res):
        try:
            bus.call_finish(res)
        except GLib.Error as err:
            finish_error(_wrap_error(err))

    def on_record_done(_source, res):
        try:
            reply = bus.call_finish(res)
        except GLib.Error as err:
            finish_error(_wrap_error(err))
            return
        state["stream_path"] = reply.unpack()[0]
        # Start より先に購読する。順序を逆にすると取りこぼす。
        state["subscription"] = bus.signal_subscribe(
            SCREEN_CAST_NAME, STREAM_IFACE, "PipeWireStreamAdded",
            state["stream_path"], None, Gio.DBusSignalFlags.NONE,
            on_stream_added, None)
        bus.call(SCREEN_CAST_NAME, state["session_path"], SESSION_IFACE,
                 "Start", None, None, Gio.DBusCallFlags.NONE, -1, None,
                 on_start_done, None)

    def on_create_done(_source, res):
        try:
            reply = bus.call_finish(res)
        except GLib.Error as err:
            finish_error(_wrap_error(err))
            return
        state["session_path"] = reply.unpack()[0]
        props = {"cursor-mode": GLib.Variant("u", protocol.to_mutter_cursor_mode(cursor_mode))}
        bus.call(SCREEN_CAST_NAME, state["session_path"], SESSION_IFACE,
                 "RecordMonitor", GLib.Variant("(sa{sv})", (connector, props)),
                 GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, -1, None,
                 on_record_done, None)

    state["timeout_id"] = GLib.timeout_add(timeout_ms, on_timeout)
    bus.call(SCREEN_CAST_NAME, SCREEN_CAST_PATH, SCREEN_CAST_IFACE,
             "CreateSession", GLib.Variant("(a{sv})", ({},)),
             GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, -1, None,
             on_create_done, None)


def _cleanup(bus, state):
    if state["timeout_id"] is not None:
        GLib.source_remove(state["timeout_id"])
        state["timeout_id"] = None
    if state["subscription"] is not None:
        bus.signal_unsubscribe(state["subscription"])
        state["subscription"] = None
    if state["session_path"] is not None:
        bus.call(SCREEN_CAST_NAME, state["session_path"], SESSION_IFACE,
                 "Stop", None, None, Gio.DBusCallFlags.NONE, -1, None, None, None)
        state["session_path"] = None
