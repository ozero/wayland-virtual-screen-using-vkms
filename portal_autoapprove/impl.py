"""org.freedesktop.impl.portal.ScreenCast の D-Bus 面。

ポリシーに合致した要求は Mutter を直接叩いて UI 無しでストリームを返す(承認パス)。
合致しない要求は従来どおり xdg-desktop-portal-gnome へ中継する(中継パス)。
判定は CreateSession のときに一度だけ行い、そのセッションの経路を固定する。
"""
import logging
import time

from gi.repository import Gio, GLib

from portal_autoapprove import monitors, mutter, policy, protocol, proxy

ROUTE_APPROVE = "approve"
ROUTE_DELEGATE = "delegate"

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
    """1つの画面共有セッション。

    route は CreateSession のときに決める。SelectSources や Start で決め直すと、
    中継経路に入るべきセッションが GNOME 側に存在しないことになるため。
    例外は承認に失敗したときの中継フォールバックで、そこでは元の引数を
    GNOME へ流し直したうえで route を切り替える(_delegate_from_scratch)。
    """

    def __init__(self, backend, path, route, reason):
        self.backend = backend
        self.path = path
        self.route = route
        self.reason = reason
        self.cursor_mode = protocol.CURSOR_MODE_HIDDEN
        self.recording = None
        self.registration_id = None
        self.closed_subscription = None
        # 承認経路で CreateSession/SelectSources に渡された元の引数。Mutter が
        # 最終的に失敗して中継に切り替えるとき、GNOME バックエンドに対して
        # CreateSession/SelectSources を最初から張り直すために使う
        # (承認経路ではこれらを GNOME に送っていないため)。
        self.create_params = None
        self.select_params = None

    def close(self):
        if self.recording is not None:
            self.recording.stop()
            self.recording = None
        if self.route == ROUTE_DELEGATE:
            self.backend.fallback.close_object(self.path, SESSION_IFACE)


class ScreenCastBackend:
    def __init__(self, bus, fallback_backend_name, prefer_connector, mode,
                 retry_seconds, policy_grace_ms):
        self.bus = bus
        self.fallback = proxy.Backend(bus, fallback_backend_name)
        self.prefer_connector = prefer_connector
        self.mode = mode
        self.retry_seconds = retry_seconds
        self.policy_grace_ms = policy_grace_ms
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
        self._export_request(handle)
        deadline = time.monotonic() + self.policy_grace_ms / 1000.0

        def settle():
            decision = policy.decide(self.mode, policy.is_rustdesk_connected())
            if (not decision.approve
                    and self.mode == policy.MODE_RUSTDESK_CONNECTED
                    and time.monotonic() < deadline):
                # rustdesk --cm は ScreenCast 要求と同じ秒に起動する(実測)。要求の方が
                # わずかに先だっただけの取りこぼしを防ぐため、少し待って見直す。
                GLib.timeout_add(250, settle)
                return GLib.SOURCE_REMOVE

            route = ROUTE_APPROVE if decision.approve else ROUTE_DELEGATE
            self.log.info('req=CreateSession session=%s app_id="%s" decision=%s reason=%s',
                          session_handle, app_id, route, decision.reason)

            if route == ROUTE_DELEGATE:
                def on_reply(response):
                    self._release_request(handle)
                    # 中継先が失敗した場合、上流にセッションは存在しない。こちらだけ
                    # export すると Close() も Closed も来ないまま残り続けるので、
                    # 成功したときだけ export する。
                    if response == protocol.RESPONSE_SUCCESS:
                        self._export_session(session_handle, route, decision.reason)

                self.fallback.forward("CreateSession", params, invocation, on_reply)
                return GLib.SOURCE_REMOVE

            self._export_session(session_handle, route, decision.reason)
            # 中継に切り替えることになった場合に GNOME へ張り直せるよう、元の
            # 引数を覚えておく。
            self._sessions[session_handle].create_params = params
            invocation.return_value(
                GLib.Variant("(ua{sv})", (protocol.RESPONSE_SUCCESS, {})))
            self._release_request(handle)
            return GLib.SOURCE_REMOVE

        settle()

    def _select_sources(self, params, invocation):
        handle, session_handle, _app_id, options = params.unpack()
        self._export_request(handle)
        session = self._sessions.get(session_handle)

        if session is None or session.route == ROUTE_DELEGATE:
            self.fallback.forward("SelectSources", params, invocation,
                                  lambda _response: self._release_request(handle))
            return

        # 要求された cursor_mode をそのまま覚える。勝手に変えない。
        session.cursor_mode = options.get("cursor_mode", protocol.CURSOR_MODE_HIDDEN)
        self.log.info("req=SelectSources session=%s cursor_mode=%d",
                      session_handle, session.cursor_mode)
        # 中継に切り替えることになった場合に GNOME へ張り直せるよう、元の
        # 引数を覚えておく。
        session.select_params = params
        invocation.return_value(
            GLib.Variant("(ua{sv})", (protocol.RESPONSE_SUCCESS, {})))
        self._release_request(handle)

    def _start(self, params, invocation):
        handle, session_handle, app_id, _parent, _options = params.unpack()
        self._export_request(handle)
        session = self._sessions.get(session_handle)

        if session is None or session.route == ROUTE_DELEGATE:
            self.fallback.forward("Start", params, invocation,
                                  lambda _response: self._release_request(handle))
            return

        started = time.monotonic()
        try:
            state = mutter.get_current_state(self.bus)
            connector = monitors.select_connector(state, self.prefer_connector)
            position, size = monitors.stream_geometry(state, connector)
        except (GLib.Error, ValueError, KeyError) as exc:
            # モニタ構成が変わった瞬間などに起こりうる。例外をハンドラの外へ
            # 逃がすと応答が返らずクライアントがハングし、Request も残る。
            self.log.error('req=Start session=%s app_id="%s" decision=approve '
                           "error=%s", session_handle, app_id, exc)
            self._release_request(handle)
            invocation.return_value(
                GLib.Variant("(ua{sv})", (protocol.RESPONSE_OTHER, {})))
            return

        def on_ready(recording):
            if self._sessions.get(session_handle) is not session:
                # record_monitor の実行中に Session.Close が来た。ここで止めないと
                # 誰も所有しない録画が残り、Mutter の Closed も _session_closed に
                # 吸われて誰も stop できなくなる。
                recording.stop()
                self.log.info("req=Start session=%s 記録開始前にセッションが閉じられた",
                              session_handle)
                self._release_request(handle)
                invocation.return_value(
                    GLib.Variant("(ua{sv})", (protocol.RESPONSE_CANCELLED, {})))
                return
            session.recording = recording
            recording.connect_closed(lambda: self._session_closed(session_handle))
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self.log.info('req=Start session=%s app_id="%s" decision=approve reason=%s '
                          "connector=%s cursor_mode=%d node_id=%d elapsed=%dms",
                          session_handle, app_id, session.reason, connector,
                          session.cursor_mode, recording.node_id, elapsed_ms)
            stream_props = {
                "position": GLib.Variant("(ii)", position),
                "size": GLib.Variant("(ii)", size),
                "source_type": GLib.Variant("u", protocol.SOURCE_TYPE_MONITOR),
            }
            results = {
                "streams": GLib.Variant("a(ua{sv})",
                                        [(recording.node_id, stream_props)]),
            }
            invocation.return_value(
                GLib.Variant("(ua{sv})", (protocol.RESPONSE_SUCCESS, results)))
            self._release_request(handle)

        # Mutter が "Session creation inhibited" を返した場合、期限内なら少し待って
        # 再試行する。RustDesk は接続時に uinput で入力を注入するので、画面ブランク
        # 由来の inhibit ならこの待ちの間に解除されることがある(実機で確認済み。
        # 現在は idle-delay=0 で inhibit 自体が起きないようにしてあるので保険)。
        # 再試行の余地が無い、または InhibitedError 以外の失敗なら、無人運用の
        # マシンで「拒否」のままハード失敗させないよう、GNOME バックエンドへの
        # 中継(=従来どおりダイアログ)に切り替える。
        deadline = time.monotonic() + self.retry_seconds

        def attempt():
            mutter.record_monitor(self.bus, connector, session.cursor_mode,
                                  on_ready, on_error)
            return GLib.SOURCE_REMOVE

        def on_error(exc):
            if self._sessions.get(session_handle) is not session:
                self.log.info("req=Start session=%s 失敗したがセッションは既に閉じられている",
                              session_handle)
                self._release_request(handle)
                invocation.return_value(
                    GLib.Variant("(ua{sv})", (protocol.RESPONSE_CANCELLED, {})))
                return
            if isinstance(exc, mutter.InhibitedError) and time.monotonic() < deadline:
                self.log.info("req=Start session=%s inhibited、500ms 後に再試行",
                              session_handle)
                GLib.timeout_add(500, attempt)
                return
            self.log.warning('req=Start session=%s app_id="%s" decision=approve '
                             "connector=%s error=%s → 中継に切り替える",
                             session_handle, app_id, connector, exc)
            self._delegate_from_scratch(session, params, invocation, handle)

        attempt()

    def _delegate_from_scratch(self, session, start_params, invocation, handle):
        """承認に失敗したセッションを GNOME バックエンドへ張り直して中継する。

        承認経路では CreateSession / SelectSources を GNOME に送っていないので、
        Start だけ中継しても向こうにセッションが無い。保存しておいた元の引数を
        順番に流し直してから Start を中継する。どの終了経路でも Request をちょうど
        1回だけ解放する。
        """
        session.route = ROUTE_DELEGATE

        if session.create_params is None or session.select_params is None:
            self.log.error("session=%s 元の引数が無く中継に切り替えられない",
                           session.path)
            invocation.return_value(
                GLib.Variant("(ua{sv})", (protocol.RESPONSE_OTHER, {})))
            self._release_request(handle)
            return

        def fail(stage, response):
            self.log.error("session=%s 中継の %s が response=%d を返した",
                           session.path, stage, response)
            invocation.return_value(GLib.Variant("(ua{sv})", (response, {})))
            self._release_request(handle)

        def on_start_done(_source, res):
            try:
                invocation.return_value(self.bus.call_finish(res))
            except GLib.Error as err:
                invocation.return_gerror(err)
            self._release_request(handle)

        def on_select_done(_source, res):
            try:
                response = self.bus.call_finish(res).unpack()[0]
            except GLib.Error as err:
                invocation.return_gerror(err)
                self._release_request(handle)
                return
            if response != protocol.RESPONSE_SUCCESS:
                fail("SelectSources", response)
                return
            # Start はダイアログを出しうるので、GDBus の既定値(-1 = 25秒)ではなく
            # GLib.MAXINT で無制限に待つ(Fix 2、proxy.forward と同じ理由)。
            self.bus.call(self.fallback.name, PORTAL_PATH,
                          proxy.SCREEN_CAST_IFACE, "Start", start_params,
                          proxy.REPLY_TYPE, Gio.DBusCallFlags.NONE,
                          GLib.MAXINT, None, on_start_done)

        def on_create_done(_source, res):
            try:
                response = self.bus.call_finish(res).unpack()[0]
            except GLib.Error as err:
                invocation.return_gerror(err)
                self._release_request(handle)
                return
            if response != protocol.RESPONSE_SUCCESS:
                fail("CreateSession", response)
                return
            # 中継先にセッションができてから購読する。失敗したまま購読すると、
            # Close も Closed も来ないまま登録が残る(Task 6 で同じ誤りを直した)。
            session.closed_subscription = self.fallback.subscribe_closed(
                session.path, lambda: self._session_closed(session.path))
            self.bus.call(self.fallback.name, PORTAL_PATH,
                          proxy.SCREEN_CAST_IFACE, "SelectSources",
                          session.select_params, proxy.REPLY_TYPE,
                          Gio.DBusCallFlags.NONE, -1, None, on_select_done)

        self.bus.call(self.fallback.name, PORTAL_PATH, proxy.SCREEN_CAST_IFACE,
                      "CreateSession", session.create_params, proxy.REPLY_TYPE,
                      Gio.DBusCallFlags.NONE, -1, None, on_create_done)

    # ---- Session / Request オブジェクト ----

    def _export_session(self, path, route, reason):
        if path in self._sessions:
            return
        session = Session(self, path, route, reason)
        session.registration_id = self.bus.register_object(
            path, self._session_info.interfaces[0],
            self._on_session_method_call, None, None)
        if route == ROUTE_DELEGATE:
            # 中継先の Session が閉じたら、こちらの Closed も出して frontend に伝える。
            session.closed_subscription = self.fallback.subscribe_closed(
                path, lambda: self._session_closed(path))
        self._sessions[path] = session

    def _session_closed(self, path):
        session = self._sessions.pop(path, None)
        if session is None:
            return
        if session.recording is not None:
            # Mutter 側から閉じられた場合もここを通る。Recording が持つ Closed の
            # 購読を外さないとセッションごとに溜まる。stop() は二重呼び出しでも無害。
            session.recording.stop()
            session.recording = None
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

    def _release_request(self, path):
        """応答を返し終えた Request を後片付けする。

        impl.portal.Request の Close() は「処理中の要求を取り消す」ときにしか
        呼ばれない。正常に応答を返した場合は誰も Close() しないので、ここで
        自分で外さないと常駐デーモンに D-Bus オブジェクトが溜まり続ける。
        """
        registration_id = self._requests.pop(path, None)
        if registration_id is not None:
            self.bus.unregister_object(registration_id)

    def _on_request_method_call(self, _conn, _sender, path, _iface, method,
                                _params, invocation):
        if method != "Close":
            invocation.return_error_literal(
                Gio.DBusError.quark(), Gio.DBusError.UNKNOWN_METHOD, method)
            return
        self.fallback.close_object(path, REQUEST_IFACE)
        invocation.return_value(None)
        self._release_request(path)
