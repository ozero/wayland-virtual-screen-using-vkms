"""impl.py の Session/Request ライフサイクルを D-Bus 無しで検証する。

中継専用の骨格でも、要求ごとに登録した D-Bus オブジェクトを外し忘れると
常駐デーモンに溜まり続ける。にせ bus で登録と解除の対応を数える。
"""
import unittest

from gi.repository import GLib

from portal_autoapprove import impl, policy

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

    def call(self, _name, path, _iface, method, _params, _reply_type, _flags,
             _timeout, _cancellable, callback=None):
        self.calls.append({"method": method, "path": path, "callback": callback})

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


if __name__ == "__main__":
    unittest.main()
