"""record_monitor の状態遷移を D-Bus 無しで検証する。

にせの bus に応答を保留させ、テストが任意の順序でコールバックを発火させることで
「打ち切った後に応答が届く」レースを決定的に再現する。
"""
import unittest
from unittest import mock

from gi.repository import GLib

from portal_autoapprove import mutter, protocol


class FakeBus:
    """call() の応答を即座に返さず保留する最小のにせ bus。"""

    def __init__(self):
        self.calls = []
        self.subscriptions = {}
        self.unsubscribed = []
        self._next_sub = 1

    def call(self, _name, path, _iface, method, _params, _reply_type, _flags,
             _timeout, _cancellable, callback=None):
        self.calls.append({"method": method, "path": path, "callback": callback})

    def call_finish(self, res):
        # テストは reply の Variant をそのまま res として渡す
        return res

    def signal_subscribe(self, _sender, _iface, member, path, _arg0, _flags,
                         callback):
        sub = self._next_sub
        self._next_sub += 1
        self.subscriptions[sub] = {"member": member, "path": path,
                                   "callback": callback}
        return sub

    def signal_unsubscribe(self, sub):
        self.unsubscribed.append(sub)
        self.subscriptions.pop(sub, None)

    def methods(self):
        return [c["method"] for c in self.calls]

    def find(self, method):
        return [c for c in self.calls if c["method"] == method]


class FakeTimers:
    """GLib.timeout_add / source_remove の差し替え。"""

    def __init__(self):
        self.callbacks = []
        self.removed = []

    def add(self, _interval, callback):
        self.callbacks.append(callback)
        return len(self.callbacks)

    def remove(self, source_id):
        self.removed.append(source_id)

    def fire(self):
        self.callbacks[-1]()


class RecordMonitorTestCase(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.timers = FakeTimers()
        self.ready = []
        self.errors = []
        patcher_add = mock.patch.object(mutter.GLib, "timeout_add", self.timers.add)
        patcher_rm = mock.patch.object(mutter.GLib, "source_remove", self.timers.remove)
        patcher_add.start()
        patcher_rm.start()
        self.addCleanup(patcher_add.stop)
        self.addCleanup(patcher_rm.stop)

    def start(self):
        mutter.record_monitor(self.bus, "Virtual-1",
                              protocol.CURSOR_MODE_EMBEDDED,
                              self.ready.append, self.errors.append,
                              timeout_ms=1)

    def reply_object(self, path):
        return GLib.Variant("(o)", (path,))


class TestHappyPath(RecordMonitorTestCase):
    def test_full_sequence_yields_recording_with_node_id(self):
        self.start()
        self.assertEqual(self.bus.methods(), ["CreateSession"])

        self.bus.find("CreateSession")[0]["callback"](
            None, self.reply_object("/session/u1"))
        self.assertEqual(self.bus.methods(), ["CreateSession", "RecordMonitor"])

        self.bus.find("RecordMonitor")[0]["callback"](
            None, self.reply_object("/stream/u1"))
        self.assertEqual(self.bus.methods(),
                         ["CreateSession", "RecordMonitor", "Start"])

        # Start より先に購読していること(取りこぼし防止)
        self.assertEqual(len(self.bus.subscriptions), 1)
        sub = next(iter(self.bus.subscriptions.values()))
        self.assertEqual(sub["member"], "PipeWireStreamAdded")

        self.bus.find("Start")[0]["callback"](None, None)
        sub["callback"](None, None, "/stream/u1", None, "PipeWireStreamAdded",
                        GLib.Variant("(u)", (75,)))

        self.assertEqual(len(self.ready), 1)
        self.assertEqual(self.ready[0].node_id, 75)
        self.assertEqual(self.errors, [])
        self.assertNotIn("Stop", self.bus.methods())


class TestTimeoutRace(RecordMonitorTestCase):
    def test_late_create_reply_stops_the_orphaned_session(self):
        self.start()
        create = self.bus.find("CreateSession")[0]

        self.timers.fire()
        self.assertEqual(len(self.errors), 1)

        # 打ち切った後に CreateSession の応答が届く
        create["callback"](None, self.reply_object("/session/u1"))

        self.assertNotIn("RecordMonitor", self.bus.methods())
        self.assertEqual([c["path"] for c in self.bus.find("Stop")],
                         ["/session/u1"])
        self.assertEqual(self.ready, [])

    def test_late_record_reply_stops_the_session_and_does_not_start(self):
        self.start()
        self.bus.find("CreateSession")[0]["callback"](
            None, self.reply_object("/session/u1"))
        record = self.bus.find("RecordMonitor")[0]

        self.timers.fire()
        self.assertEqual(len(self.errors), 1)
        # 打ち切り時点で session_path が判明しているので _cleanup が止めている
        self.assertEqual([c["path"] for c in self.bus.find("Stop")],
                         ["/session/u1"])

        # 打ち切った後に RecordMonitor の応答が届いても Start しない
        record["callback"](None, self.reply_object("/stream/u1"))
        self.assertNotIn("Start", self.bus.methods())
        self.assertEqual(self.ready, [])

    def test_no_ready_callback_after_timeout(self):
        self.start()
        self.timers.fire()
        self.bus.find("CreateSession")[0]["callback"](
            None, self.reply_object("/session/u1"))
        self.assertEqual(self.ready, [])
        self.assertEqual(len(self.errors), 1)


if __name__ == "__main__":
    unittest.main()
