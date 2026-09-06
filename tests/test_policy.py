"""policy.py の単体テスト。"""
import os
import shutil
import tempfile
import unittest

from portal_autoapprove import policy


class TestDecide(unittest.TestCase):
    def test_never_always_delegates(self):
        d = policy.decide(policy.MODE_NEVER, rustdesk_connected=True)
        self.assertFalse(d.approve)
        self.assertEqual(d.reason, "mode-never")

    def test_always_approves_regardless(self):
        d = policy.decide(policy.MODE_ALWAYS, rustdesk_connected=False)
        self.assertTrue(d.approve)
        self.assertEqual(d.reason, "mode-always")

    def test_rustdesk_connected_approves_when_cm_running(self):
        d = policy.decide(policy.MODE_RUSTDESK_CONNECTED, rustdesk_connected=True)
        self.assertTrue(d.approve)
        self.assertEqual(d.reason, "rustdesk-cm-running")

    def test_rustdesk_connected_delegates_when_cm_absent(self):
        d = policy.decide(policy.MODE_RUSTDESK_CONNECTED, rustdesk_connected=False)
        self.assertFalse(d.approve)
        self.assertEqual(d.reason, "rustdesk-not-connected")

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            policy.decide("whatever", rustdesk_connected=True)


class TestIsRustdeskConnected(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)

    def _add_process(self, pid, argv):
        d = os.path.join(self.root, str(pid))
        os.makedirs(d)
        with open(os.path.join(d, "cmdline"), "wb") as f:
            f.write(b"\0".join(a.encode() for a in argv) + b"\0")

    def test_detects_rustdesk_cm(self):
        self._add_process(101, ["/usr/share/rustdesk/rustdesk", "--cm"])
        self.assertTrue(policy.is_rustdesk_connected(self.root))

    def test_ignores_rustdesk_server_without_cm(self):
        self._add_process(102, ["/usr/share/rustdesk/rustdesk", "--server"])
        self.assertFalse(policy.is_rustdesk_connected(self.root))

    def test_ignores_unrelated_process_with_cm_flag(self):
        self._add_process(103, ["/usr/bin/somethingelse", "--cm"])
        self.assertFalse(policy.is_rustdesk_connected(self.root))

    def test_ignores_non_numeric_entries(self):
        os.makedirs(os.path.join(self.root, "self"))
        self.assertFalse(policy.is_rustdesk_connected(self.root))

    def test_tolerates_process_that_disappears(self):
        d = os.path.join(self.root, "104")
        os.makedirs(d)  # cmdline を作らない = 読めないプロセス
        self.assertFalse(policy.is_rustdesk_connected(self.root))


if __name__ == "__main__":
    unittest.main()
