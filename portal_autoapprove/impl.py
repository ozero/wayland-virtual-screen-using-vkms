"""org.freedesktop.impl.portal.ScreenCast の D-Bus 面。

この版はすべての要求を中継先バックエンドへそのまま渡す。バックエンドを差し込んでも
挙動が変わらないことを先に確かめるため。承認パスは次のタスクで足す。
"""
import logging

from gi.repository import Gio, GLib

from portal_autoapprove import proxy

BUS_NAME = "org.freedesktop.impl.portal.desktop.autoapprove"
PORTAL_PATH = "/org/freedesktop/portal/desktop"

SESSION_IFACE = "org.freedesktop.impl.portal.Session"
REQUEST_IFACE = "org.freedesktop.impl.portal.Request"

# MONITOR のみ。WINDOW / VIRTUAL は自動承認する設計になっていないので名乗らない。
AVAILABLE_SOURCE_TYPES = 1
# HIDDEN | EMBEDDED | METADATA
AVAILABLE_CURSOR_MODES = 7
VERSION = 5

SCREEN_CAST_XML = """
<node>
  <interface name="org.freedesktop.impl.portal.ScreenCast">
    <method name="CreateSession">
      <arg type="o" name="handle" direction="in"/>
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="s" name="app_id" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/>
      <arg type="a{sv}" name="results" direction="out"/>
    </method>
    <method name="SelectSources">
      <arg type="o" name="handle" direction="in"/>
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="s" name="app_id" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/>
      <arg type="a{sv}" name="results" direction="out"/>
    </method>
    <method name="Start">
      <arg type="o" name="handle" direction="in"/>
      <arg type="o" name="session_handle" direction="in"/>
      <arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/>
      <arg type="a{sv}" name="results" direction="out"/>
    </method>
    <property name="AvailableSourceTypes" type="u" access="read"/>
    <property name="AvailableCursorModes" type="u" access="read"/>
    <property name="version" type="u" access="read"/>
  </interface>
</node>
"""

SESSION_XML = """
<node>
  <interface name="org.freedesktop.impl.portal.Session">
    <method name="Close"/>
    <signal name="Closed"/>
  </interface>
</node>
"""

REQUEST_XML = """
<node>
  <interface name="org.freedesktop.impl.portal.Request">
    <method name="Close"/>
  </interface>
</node>
"""


class Session:
    """1つの画面共有セッション。中継版はすべて中継先へ委ねる。"""

    def __init__(self, backend, path):
        self.backend = backend
        self.path = path
        self.registration_id = None
        self.closed_subscription = None

    def close(self):
        """中継先の Session を閉じる。"""
        self.backend.fallback.close_object(self.path, SESSION_IFACE)


class ScreenCastBackend:
    def __init__(self, bus, fallback_backend_name):
        self.bus = bus
        self.fallback = proxy.Backend(bus, fallback_backend_name)
        self.log = logging.getLogger("portal-autoapprove")
        self._screen_cast_info = Gio.DBusNodeInfo.new_for_xml(SCREEN_CAST_XML)
        self._session_info = Gio.DBusNodeInfo.new_for_xml(SESSION_XML)
        self._request_info = Gio.DBusNodeInfo.new_for_xml(REQUEST_XML)
        self._sessions = {}
        self._requests = {}

    def register(self):
        """/org/freedesktop/portal/desktop に ScreenCast を export する。"""
        self.bus.register_object(
            PORTAL_PATH, self._screen_cast_info.interfaces[0],
            self._on_method_call, self._on_get_property, None)
        self.log.info("registered %s at %s (fallback=%s)",
                      BUS_NAME, PORTAL_PATH, self.fallback.name)

    # ---- ScreenCast インターフェース ----

    def _on_get_property(self, _conn, _sender, _path, _iface, name):
        if name == "AvailableSourceTypes":
            return GLib.Variant("u", AVAILABLE_SOURCE_TYPES)
        if name == "AvailableCursorModes":
            return GLib.Variant("u", AVAILABLE_CURSOR_MODES)
        if name == "version":
            return GLib.Variant("u", VERSION)
        return None

    def _on_method_call(self, _conn, _sender, _path, _iface, method, params,
                        invocation):
        if method == "CreateSession":
            self._create_session(params, invocation)
        elif method == "SelectSources":
            self._select_sources(params, invocation)
        elif method == "Start":
            self._start(params, invocation)
        else:
            invocation.return_error_literal(
                Gio.DBusError.quark(), Gio.DBusError.UNKNOWN_METHOD, method)

    def _create_session(self, params, invocation):
        handle, session_handle, app_id, _options = params.unpack()
        self.log.info('req=CreateSession session=%s app_id="%s" decision=delegate '
                      'reason=proxy-only', session_handle, app_id)
        self._export_request(handle)
        self._export_session(session_handle)
        self.fallback.forward("CreateSession", params, invocation)

    def _select_sources(self, params, invocation):
        handle = params.unpack()[0]
        self._export_request(handle)
        self.fallback.forward("SelectSources", params, invocation)

    def _start(self, params, invocation):
        handle = params.unpack()[0]
        self._export_request(handle)
        self.fallback.forward("Start", params, invocation)

    # ---- Session / Request オブジェクト ----

    def _export_session(self, path):
        session = Session(self, path)
        session.registration_id = self.bus.register_object(
            path, self._session_info.interfaces[0],
            self._on_session_method_call, None, None)
        # 中継先の Session が閉じたら、こちらの Closed も出して frontend に伝える。
        session.closed_subscription = self.fallback.subscribe_closed(
            path, lambda: self._session_closed(path))
        self._sessions[path] = session

    def _session_closed(self, path):
        session = self._sessions.pop(path, None)
        if session is None:
            return
        self.bus.emit_signal(None, path, SESSION_IFACE, "Closed", None)
        if session.closed_subscription is not None:
            self.fallback.unsubscribe(session.closed_subscription)
        if session.registration_id is not None:
            self.bus.unregister_object(session.registration_id)
        self.log.info("session closed: %s", path)

    def _on_session_method_call(self, _conn, _sender, path, _iface, method,
                                _params, invocation):
        if method != "Close":
            invocation.return_error_literal(
                Gio.DBusError.quark(), Gio.DBusError.UNKNOWN_METHOD, method)
            return
        session = self._sessions.get(path)
        if session is not None:
            session.close()
        invocation.return_value(None)
        self._session_closed(path)

    def _export_request(self, path):
        if path in self._requests:
            return
        self._requests[path] = self.bus.register_object(
            path, self._request_info.interfaces[0],
            self._on_request_method_call, None, None)

    def _on_request_method_call(self, _conn, _sender, path, _iface, method,
                                _params, invocation):
        if method != "Close":
            invocation.return_error_literal(
                Gio.DBusError.quark(), Gio.DBusError.UNKNOWN_METHOD, method)
            return
        self.fallback.close_object(path, REQUEST_IFACE)
        invocation.return_value(None)
        registration_id = self._requests.pop(path, None)
        if registration_id is not None:
            self.bus.unregister_object(registration_id)
