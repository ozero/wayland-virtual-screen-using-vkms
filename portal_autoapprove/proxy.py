"""xdg-desktop-portal-gnome への中継。

impl backend は他のバックエンドへ要求を渡す仕組みを持たないので、自分で D-Bus 呼び出しを
転送する。ポリシーに合わない要求を「拒否」ではなく「従来どおりダイアログ」に落とすため
(設計書 5.1)。
"""
from gi.repository import Gio, GLib

PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREEN_CAST_IFACE = "org.freedesktop.impl.portal.ScreenCast"
SESSION_IFACE = "org.freedesktop.impl.portal.Session"
REQUEST_IFACE = "org.freedesktop.impl.portal.Request"

REPLY_TYPE = GLib.VariantType("(ua{sv})")


class Backend:
    """中継先のバックエンド。name は D-Bus の well-known name。"""

    def __init__(self, bus, name):
        self._bus = bus
        self.name = name

    def forward(self, method, params, invocation):
        """ScreenCast のメソッドをそのまま転送し、返り値で invocation に応答する。

        中継先がダイアログを出している間は返らないので timeout は無制限(-1)。
        """
        def on_done(_source, res):
            try:
                reply = self._bus.call_finish(res)
            except GLib.Error as err:
                invocation.return_gerror(err)
                return
            invocation.return_value(reply)

        self._bus.call(self.name, PORTAL_PATH, SCREEN_CAST_IFACE, method,
                       params, REPLY_TYPE, Gio.DBusCallFlags.NONE, -1, None,
                       on_done)

    def close_object(self, path, iface):
        """中継先の Session/Request の Close() を呼ぶ。応答は待たない。"""
        self._bus.call(self.name, path, iface, "Close", None, None,
                       Gio.DBusCallFlags.NONE, -1, None, None)

    def subscribe_closed(self, session_path, callback):
        """中継先の Session が閉じたら callback() を呼ぶ。購読 ID を返す。"""
        return self._bus.signal_subscribe(
            self.name, SESSION_IFACE, "Closed", session_path, None,
            Gio.DBusSignalFlags.NONE, lambda *_args: callback())

    def unsubscribe(self, subscription_id):
        self._bus.signal_unsubscribe(subscription_id)
