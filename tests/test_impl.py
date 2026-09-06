"""impl.py の Session/Request ライフサイクルを D-Bus 無しで検証する。

中継専用の骨格でも、要求ごとに登録した D-Bus オブジェクトを外し忘れると
常駐デーモンに溜まり続ける。にせ bus で登録と解除の対応を数える。
"""
import unittest
from unittest import mock

from gi.repository import GLib

from portal_autoapprove import impl, policy, protocol

REQUEST = "/org/freedesktop/portal/desktop/request/1_1/t"
SESSION = "/org/freedesktop/portal/desktop/session/1_1/s"


class FakeInvocation:
    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, variant):
        self.value = variant

    def return_gerror(self, err):
        self.error = err

    def return_error_literal(self, domain, code, message):
        self.error = (domain, code, message)


class FakeBus:
    """register_object / signal_subscribe の登録と解除を数えるにせ bus。"""

    def __init__(self):
        self.calls = []
        self.registered = {}
        self.unregistered = []
        self.subscriptions = {}
        self.unsubscribed = []
        self.signals = []
        self._next_id = 1

    def _take_id(self):
        i = self._next_id
        self._next_id += 1
        return i

    def call(self, name, path, _iface, method, params, _reply_type, _flags,
             _timeout, _cancellable, callback=None):
        self.calls.append({"method": method, "path": path, "name": name,
                           "params": params, "callback": callback})

    def call_finish(self, res):
        return res

    def register_object(self, path, _iface_info, _method_call,
                        _get_property=None, _set_property=None):
        i = self._take_id()
        self.registered[i] = path
        return i

    def unregister_object(self, registration_id):
        self.unregistered.append(registration_id)
        self.registered.pop(registration_id, None)

    def signal_subscribe(self, _sender, _iface, member, path, _arg0, _flags,
                         callback):
        i = self._take_id()
        self.subscriptions[i] = {"member": member, "path": path,
                                 "callback": callback}
        return i

    def signal_unsubscribe(self, sub):
        self.unsubscribed.append(sub)
        self.subscriptions.pop(sub, None)

    def emit_signal(self, _dest, path, iface, name, _params):
        self.signals.append((path, iface, name))

    def live_paths(self):
        return sorted(self.registered.values())


def create_params():
    return GLib.Variant("(oosa{sv})", (REQUEST, SESSION, "", {}))


def select_params():
    return GLib.Variant("(oosa{sv})", (REQUEST, SESSION, "", {}))


def select_params_with_cursor(mode=2):
    return GLib.Variant("(oosa{sv})", (REQUEST, SESSION, "",
                                       {"cursor_mode": GLib.Variant("u", mode)}))


def start_params():
    return GLib.Variant("(oossa{sv})", (REQUEST, SESSION, "", "", {}))


def ok_reply():
    return GLib.Variant("(ua{sv})", (0, {}))


def denied_reply():
    return GLib.Variant("(ua{sv})", (1, {}))


class ImplTestCase(unittest.TestCase):
    def setUp(self):
        # mode=MODE_NEVER: このファイルは中継(delegate)経路の Session/Request
        # ライフサイクルだけを見る。承認(approve)経路は mutter/monitors への実 D-Bus
        # 呼び出しを要するのでここでは対象にせず、ポリシー判定そのものは
        # test_policy.py で検証済み。retry_seconds/policy_grace_ms は
        # MODE_NEVER では参照されないので 0 でよい。
        self.bus = FakeBus()
        self.backend = impl.ScreenCastBackend(
            self.bus, "org.freedesktop.impl.portal.desktop.gnome",
            "Virtual-1", policy.MODE_NEVER, 0, 0)
        self.backend.register()

    def call_method(self, method, params):
        invocation = FakeInvocation()
        self.backend._on_method_call(None, None, impl.PORTAL_PATH, None,
                                     method, params, invocation)
        return invocation

    def last_forward(self):
        return self.bus.calls[-1]


class TestRequestLifecycle(ImplTestCase):
    def test_request_is_released_after_a_normal_reply(self):
        invocation = self.call_method("CreateSession", create_params())
        self.assertIn(REQUEST, self.bus.live_paths())

        forwarded = self.last_forward()
        self.assertEqual(forwarded["method"], "CreateSession")
        forwarded["callback"](None, ok_reply())

        self.assertEqual(invocation.value.unpack(), (0, {}))
        self.assertNotIn(REQUEST, self.bus.live_paths())

    def test_request_is_released_even_when_the_transport_fails(self):
        self.call_method("SelectSources", select_params())
        self.assertIn(REQUEST, self.bus.live_paths())

        def raising_call_finish(_res):
            raise GLib.Error("boom")

        self.bus.call_finish = raising_call_finish
        self.last_forward()["callback"](None, None)

        self.assertNotIn(REQUEST, self.bus.live_paths())

    def test_full_delegated_flow_leaves_only_the_session(self):
        for method, params in (("CreateSession", create_params()),
                               ("SelectSources", select_params()),
                               ("Start", start_params())):
            self.call_method(method, params)
            self.last_forward()["callback"](None, ok_reply())

        self.assertNotIn(REQUEST, self.bus.live_paths())
        self.assertIn(SESSION, self.bus.live_paths())


class TestSessionLifecycle(ImplTestCase):
    def test_session_is_exported_when_create_session_succeeds(self):
        self.call_method("CreateSession", create_params())
        self.last_forward()["callback"](None, ok_reply())
        self.assertIn(SESSION, self.bus.live_paths())
        self.assertEqual(len(self.bus.subscriptions), 1)

    def test_nothing_is_left_when_create_session_is_denied(self):
        self.call_method("CreateSession", create_params())
        self.last_forward()["callback"](None, denied_reply())
        self.assertNotIn(SESSION, self.bus.live_paths())
        self.assertNotIn(REQUEST, self.bus.live_paths())
        self.assertEqual(self.bus.subscriptions, {})

    def test_closing_unregisters_unsubscribes_and_emits_closed(self):
        self.call_method("CreateSession", create_params())
        self.last_forward()["callback"](None, ok_reply())

        invocation = FakeInvocation()
        self.backend._on_session_method_call(None, None, SESSION, None, "Close",
                                             None, invocation)

        self.assertIn((SESSION, impl.SESSION_IFACE, "Closed"), self.bus.signals)
        self.assertNotIn(SESSION, self.bus.live_paths())
        self.assertEqual(self.bus.subscriptions, {})

    def test_closing_twice_emits_closed_only_once(self):
        self.call_method("CreateSession", create_params())
        self.last_forward()["callback"](None, ok_reply())
        self.backend._session_closed(SESSION)
        self.backend._session_closed(SESSION)

        closed = [s for s in self.bus.signals if s[2] == "Closed"]
        self.assertEqual(len(closed), 1)


class TestScreenCastInterface(ImplTestCase):
    def test_unknown_method_returns_an_error(self):
        invocation = self.call_method("Nope", create_params())
        self.assertIsNotNone(invocation.error)
        self.assertIsNone(invocation.value)

    def test_published_properties(self):
        get = self.backend._on_get_property
        self.assertEqual(
            get(None, None, impl.PORTAL_PATH, None, "AvailableSourceTypes").unpack(), 1)
        self.assertEqual(
            get(None, None, impl.PORTAL_PATH, None, "AvailableCursorModes").unpack(), 7)
        self.assertEqual(
            get(None, None, impl.PORTAL_PATH, None, "version").unpack(), 5)
        self.assertIsNone(get(None, None, impl.PORTAL_PATH, None, "nope"))


class FakeRecording:
    def __init__(self, node_id=77):
        self.node_id = node_id
        self.stopped = False
        self.closed_cb = None

    def connect_closed(self, callback):
        self.closed_cb = callback

    def stop(self):
        self.stopped = True


class ApproveTestCase(ImplTestCase):
    """承認経路。mutter と monitors を差し替えて D-Bus 無しで駆動する。"""

    def setUp(self):
        super().setUp()
        self.backend.mode = policy.MODE_ALWAYS
        self.recording = FakeRecording()
        self.record_calls = []
        self.on_ready = None
        self.on_error = None

        def fake_record_monitor(_bus, connector, cursor_mode, on_ready, on_error,
                                timeout_ms=5000):
            self.record_calls.append((connector, cursor_mode))
            self.on_ready, self.on_error = on_ready, on_error

        for target, name, value in (
                (impl.mutter, "get_current_state", lambda _bus: "state"),
                (impl.mutter, "record_monitor", fake_record_monitor),
                (impl.monitors, "select_connector", lambda _s, prefer: prefer),
                (impl.monitors, "stream_geometry", lambda _s, _c: ((0, 0), (3840, 2160)))):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def approve_session(self, cursor_mode=2):
        self.call_method("CreateSession", create_params())
        self.call_method("SelectSources", select_params_with_cursor(cursor_mode))
        return self.backend._sessions[SESSION]


class TestApprovePath(ApproveTestCase):
    def test_route_is_approve_and_fixed_at_create_session(self):
        session = self.approve_session()
        self.assertEqual(session.route, impl.ROUTE_APPROVE)
        self.assertEqual(self.bus.calls, [])   # 中継はしていない

    def test_start_returns_only_streams_and_releases_the_request(self):
        self.approve_session(cursor_mode=2)
        invocation = self.call_method("Start", start_params())
        self.assertEqual(self.record_calls, [("Virtual-1", 2)])

        self.on_ready(self.recording)

        response, results = invocation.value.unpack()
        self.assertEqual(response, protocol.RESPONSE_SUCCESS)
        self.assertEqual(sorted(results), ["streams"])   # restore_data を返さない
        node_id, props = results["streams"][0]
        self.assertEqual(node_id, 77)
        self.assertEqual(props["position"], (0, 0))
        self.assertEqual(props["size"], (3840, 2160))
        self.assertEqual(props["source_type"], protocol.SOURCE_TYPE_MONITOR)
        self.assertNotIn(REQUEST, self.bus.live_paths())

    def test_requested_cursor_mode_is_passed_through(self):
        self.approve_session(cursor_mode=protocol.CURSOR_MODE_METADATA)
        self.call_method("Start", start_params())
        self.assertEqual(self.record_calls[0][1], protocol.CURSOR_MODE_METADATA)


class TestApprovePathFailures(ApproveTestCase):
    def test_replies_and_releases_when_mutter_is_unavailable(self):
        self.approve_session()
        with mock.patch.object(impl.mutter, "get_current_state",
                               side_effect=GLib.Error("mutter unavailable")):
            invocation = self.call_method("Start", start_params())
        self.assertEqual(invocation.value.unpack(),
                         (protocol.RESPONSE_OTHER, {}))
        self.assertNotIn(REQUEST, self.bus.live_paths())

    def test_replies_and_releases_when_no_monitor_is_connected(self):
        self.approve_session()
        with mock.patch.object(impl.monitors, "select_connector",
                               side_effect=ValueError("接続中のモニタが1つも無い")):
            invocation = self.call_method("Start", start_params())
        self.assertEqual(invocation.value.unpack()[0], protocol.RESPONSE_OTHER)
        self.assertNotIn(REQUEST, self.bus.live_paths())

    def test_replies_and_releases_when_the_connector_has_no_current_mode(self):
        self.approve_session()
        with mock.patch.object(impl.monitors, "stream_geometry",
                               side_effect=KeyError("Virtual-1")):
            invocation = self.call_method("Start", start_params())
        self.assertEqual(invocation.value.unpack()[0], protocol.RESPONSE_OTHER)
        self.assertNotIn(REQUEST, self.bus.live_paths())

    def test_non_inhibited_record_monitor_error_delegates_without_retrying(self):
        """InhibitedError 以外の失敗はリトライせず即座に中継へ切り替わる。

        リトライ/中継フォールバックの本体は TestInhibitedRetry /
        TestDelegateFallback で検証する。ここでは、承認経路の最終手段が
        「拒否で終わる」から「中継に切り替える」に変わったことだけを確かめる。
        """
        self.approve_session()
        with mock.patch.object(impl.GLib, "timeout_add") as fake_timeout_add:
            invocation = self.call_method("Start", start_params())
            self.on_error(RuntimeError("boom"))

        fake_timeout_add.assert_not_called()
        self.assertEqual(len(self.record_calls), 1)   # 再試行していない
        self.assertEqual([c["method"] for c in self.bus.calls], ["CreateSession"])
        self.assertIsNone(invocation.value)
        self.assertEqual(self.backend._sessions[SESSION].route, impl.ROUTE_DELEGATE)


class TestApprovePathTeardown(ApproveTestCase):
    def test_mutter_initiated_close_stops_the_recording(self):
        self.approve_session()
        self.call_method("Start", start_params())
        self.on_ready(self.recording)
        self.assertIsNotNone(self.recording.closed_cb)

        self.recording.closed_cb()

        self.assertTrue(self.recording.stopped)
        self.assertIn((SESSION, impl.SESSION_IFACE, "Closed"), self.bus.signals)
        self.assertNotIn(SESSION, self.bus.live_paths())

    def test_client_close_stops_the_recording(self):
        self.approve_session()
        self.call_method("Start", start_params())
        self.on_ready(self.recording)

        invocation = FakeInvocation()
        self.backend._on_session_method_call(None, None, SESSION, None, "Close",
                                             None, invocation)

        self.assertTrue(self.recording.stopped)
        self.assertNotIn(SESSION, self.bus.live_paths())


class TestApprovePathClosedWhileRecording(ApproveTestCase):
    """record_monitor の実行中に Session.Close が来た場合。

    誰も所有しない Mutter の録画が残るのが最悪の結果なので、そこを固定する。
    """

    def close_session_midflight(self):
        invocation = FakeInvocation()
        self.backend._on_session_method_call(None, None, SESSION, None, "Close",
                                             None, invocation)

    def test_late_on_ready_stops_the_orphaned_recording(self):
        self.approve_session()
        start_invocation = self.call_method("Start", start_params())
        self.close_session_midflight()

        self.on_ready(self.recording)

        self.assertTrue(self.recording.stopped)
        self.assertEqual(start_invocation.value.unpack()[0],
                         protocol.RESPONSE_CANCELLED)
        self.assertNotIn(REQUEST, self.bus.live_paths())

    def test_late_on_error_does_not_create_an_orphan_delegate_session(self):
        self.approve_session()
        start_invocation = self.call_method("Start", start_params())
        self.close_session_midflight()
        before = len(self.bus.calls)

        self.on_error(RuntimeError("boom"))

        self.assertEqual(len(self.bus.calls), before)   # 中継を始めていない
        self.assertEqual(start_invocation.value.unpack()[0],
                         protocol.RESPONSE_CANCELLED)
        self.assertNotIn(REQUEST, self.bus.live_paths())


class RetryDelegateTestCase(ApproveTestCase):
    """InhibitedError のリトライと、リトライ上限超過/その他失敗での中継フォールバック。

    GLib.timeout_add と time.monotonic を差し替えて、実時間を待たずに
    「500ms 経った」「retry_seconds を使い切った」を決定的に再現する。
    タイマーの差し替え方は tests/test_mutter.py の FakeTimers を参考にした。
    """

    def setUp(self):
        super().setUp()
        self.backend.retry_seconds = 10
        self.clock = 0.0
        self.timers = []   # 予約された (interval, callback) のリスト

        def fake_timeout_add(interval, callback):
            self.timers.append((interval, callback))
            return len(self.timers)

        clock_patcher = mock.patch.object(impl.time, "monotonic", lambda: self.clock)
        timer_patcher = mock.patch.object(impl.GLib, "timeout_add", fake_timeout_add)
        clock_patcher.start()
        timer_patcher.start()
        self.addCleanup(clock_patcher.stop)
        self.addCleanup(timer_patcher.stop)

    def fire_next_timer(self):
        _interval, callback = self.timers.pop(0)
        callback()

    def start_session(self, cursor_mode=2):
        self.approve_session(cursor_mode=cursor_mode)
        return self.call_method("Start", start_params())


class TestInhibitedRetry(RetryDelegateTestCase):
    def test_retries_after_500ms_and_succeeds_once_no_longer_inhibited(self):
        invocation = self.start_session()
        self.assertEqual(len(self.record_calls), 1)

        self.on_error(impl.mutter.InhibitedError("Session creation inhibited"))

        # まだ中継はしていない。500ms 後の再試行だけが予約されている。
        self.assertEqual(self.bus.calls, [])
        self.assertEqual([interval for interval, _cb in self.timers], [500])
        self.assertIsNone(invocation.value)
        self.assertIn(REQUEST, self.bus.live_paths())   # まだ解放していない

        self.fire_next_timer()
        self.assertEqual(len(self.record_calls), 2)   # 再試行された

        self.on_ready(self.recording)
        response, results = invocation.value.unpack()
        self.assertEqual(response, protocol.RESPONSE_SUCCESS)
        self.assertEqual(results["streams"][0][0], self.recording.node_id)
        self.assertNotIn(REQUEST, self.bus.live_paths())


class TestDelegateFallback(RetryDelegateTestCase):
    def test_retry_limit_exceeded_switches_to_delegate(self):
        invocation = self.start_session()
        self.clock = 11.0   # retry_seconds=10 を使い切った

        self.on_error(impl.mutter.InhibitedError("Session creation inhibited"))

        self.assertEqual(self.timers, [])   # もう再試行は予約しない
        self.assertEqual([c["method"] for c in self.bus.calls], ["CreateSession"])
        self.assertEqual(self.backend._sessions[SESSION].route, impl.ROUTE_DELEGATE)
        self.assertIsNone(invocation.value)

    def test_non_inhibited_error_delegates_immediately_without_retrying(self):
        self.start_session()

        self.on_error(RuntimeError("boom"))

        self.assertEqual(self.timers, [])
        self.assertEqual(len(self.record_calls), 1)   # 再試行していない
        self.assertEqual([c["method"] for c in self.bus.calls], ["CreateSession"])

    def test_delegate_forwards_create_session_then_select_sources_then_start_in_order(self):
        invocation = self.start_session(cursor_mode=protocol.CURSOR_MODE_METADATA)
        self.clock = 11.0
        self.on_error(impl.mutter.InhibitedError("Session creation inhibited"))
        session = self.backend._sessions[SESSION]

        self.assertEqual(self.bus.calls[-1]["method"], "CreateSession")
        self.assertIs(self.bus.calls[-1]["params"], session.create_params)
        self.bus.calls[-1]["callback"](None, ok_reply())

        self.assertEqual(self.bus.calls[-1]["method"], "SelectSources")
        self.assertIs(self.bus.calls[-1]["params"], session.select_params)
        self.bus.calls[-1]["callback"](None, ok_reply())

        self.assertEqual(self.bus.calls[-1]["method"], "Start")
        final_reply = GLib.Variant("(ua{sv})", (protocol.RESPONSE_SUCCESS, {}))
        self.bus.calls[-1]["callback"](None, final_reply)

        self.assertEqual(invocation.value.unpack(), (protocol.RESPONSE_SUCCESS, {}))

    def test_request_is_released_exactly_once_across_the_delegated_path(self):
        invocation = self.start_session()
        self.clock = 11.0

        with mock.patch.object(self.backend, "_release_request",
                               wraps=self.backend._release_request) as released:
            self.on_error(impl.mutter.InhibitedError("Session creation inhibited"))
            self.bus.calls[-1]["callback"](None, ok_reply())    # CreateSession
            self.bus.calls[-1]["callback"](None, ok_reply())    # SelectSources
            final_reply = GLib.Variant("(ua{sv})", (protocol.RESPONSE_SUCCESS, {}))
            self.bus.calls[-1]["callback"](None, final_reply)   # Start

        released.assert_called_once_with(REQUEST)
        self.assertNotIn(REQUEST, self.bus.live_paths())
        self.assertEqual(invocation.value.unpack(), (protocol.RESPONSE_SUCCESS, {}))

    def test_select_sources_failure_stops_the_chain_and_releases_once(self):
        invocation = self.start_session()
        self.clock = 11.0

        with mock.patch.object(self.backend, "_release_request",
                               wraps=self.backend._release_request) as released:
            self.on_error(impl.mutter.InhibitedError("Session creation inhibited"))
            self.bus.calls[-1]["callback"](None, ok_reply())       # CreateSession succeeds
            self.bus.calls[-1]["callback"](None, denied_reply())   # SelectSources fails

        self.assertEqual([c["method"] for c in self.bus.calls],
                         ["CreateSession", "SelectSources"])   # Start は呼ばれない
        released.assert_called_once_with(REQUEST)
        self.assertEqual(invocation.value.unpack()[0], protocol.RESPONSE_CANCELLED)

    def test_create_session_failure_releases_once_and_does_not_subscribe(self):
        """CreateSession の replay が非成功応答を返した場合。

        中継先にセッションは存在しない。ここで Closed を購読すると、誰も
        Close()/Closed を発行しないまま登録が残り続ける。
        """
        invocation = self.start_session()
        self.clock = 11.0

        with mock.patch.object(self.backend, "_release_request",
                               wraps=self.backend._release_request) as released:
            self.on_error(impl.mutter.InhibitedError("Session creation inhibited"))
            self.assertEqual(self.bus.subscriptions, {})   # 応答前はまだ無い
            self.bus.calls[-1]["callback"](None, denied_reply())   # CreateSession fails

        self.assertEqual([c["method"] for c in self.bus.calls], ["CreateSession"])
        self.assertEqual(invocation.value.unpack()[0], protocol.RESPONSE_CANCELLED)
        released.assert_called_once_with(REQUEST)
        self.assertEqual(self.bus.subscriptions, {})

    def test_create_session_transport_error_releases_once_and_does_not_subscribe(self):
        invocation = self.start_session()
        self.clock = 11.0

        def raising_call_finish(_res):
            raise GLib.Error("boom")

        with mock.patch.object(self.backend, "_release_request",
                               wraps=self.backend._release_request) as released:
            self.on_error(impl.mutter.InhibitedError("Session creation inhibited"))
            self.bus.call_finish = raising_call_finish
            self.bus.calls[-1]["callback"](None, None)   # CreateSession の転送が失敗

        self.assertIsNotNone(invocation.error)
        self.assertIsNone(invocation.value)
        released.assert_called_once_with(REQUEST)
        self.assertEqual(self.bus.subscriptions, {})

    def test_start_transport_error_releases_once(self):
        invocation = self.start_session()
        self.clock = 11.0

        def raising_call_finish(_res):
            raise GLib.Error("boom")

        with mock.patch.object(self.backend, "_release_request",
                               wraps=self.backend._release_request) as released:
            self.on_error(impl.mutter.InhibitedError("Session creation inhibited"))
            self.bus.calls[-1]["callback"](None, ok_reply())   # CreateSession succeeds
            self.assertEqual(len(self.bus.subscriptions), 1)  # ここでは購読済みでよい
            self.bus.calls[-1]["callback"](None, ok_reply())   # SelectSources succeeds
            self.bus.call_finish = raising_call_finish
            self.bus.calls[-1]["callback"](None, None)         # Start の転送が失敗

        self.assertIsNotNone(invocation.error)
        self.assertIsNone(invocation.value)
        released.assert_called_once_with(REQUEST)

    def test_missing_original_params_returns_response_other_without_subscribing(self):
        """create_params/select_params が保存されていない防御的分岐。

        GNOME へは一切発行せず、購読も張らずに RESPONSE_OTHER で終わるはず。
        """
        invocation = self.start_session()
        self.clock = 11.0
        self.backend._sessions[SESSION].select_params = None

        with mock.patch.object(self.backend, "_release_request",
                               wraps=self.backend._release_request) as released:
            self.on_error(impl.mutter.InhibitedError("Session creation inhibited"))

        self.assertEqual(invocation.value.unpack()[0], protocol.RESPONSE_OTHER)
        released.assert_called_once_with(REQUEST)
        self.assertEqual(self.bus.subscriptions, {})
        self.assertEqual(self.bus.calls, [])   # CreateSession にすら進んでいない


class TestGracePolling(ImplTestCase):
    def test_delegates_when_the_grace_period_expires(self):
        self.backend.mode = policy.MODE_RUSTDESK_CONNECTED
        self.backend.policy_grace_ms = 0
        with mock.patch.object(impl.policy, "is_rustdesk_connected",
                               return_value=False):
            self.call_method("CreateSession", create_params())
        self.assertEqual(self.last_forward()["method"], "CreateSession")

    def test_approves_immediately_when_rustdesk_is_connected(self):
        self.backend.mode = policy.MODE_RUSTDESK_CONNECTED
        with mock.patch.object(impl.policy, "is_rustdesk_connected",
                               return_value=True):
            invocation = self.call_method("CreateSession", create_params())
        self.assertEqual(invocation.value.unpack(), (protocol.RESPONSE_SUCCESS, {}))
        self.assertEqual(self.backend._sessions[SESSION].route, impl.ROUTE_APPROVE)
        self.assertEqual(self.bus.calls, [])


if __name__ == "__main__":
    unittest.main()
