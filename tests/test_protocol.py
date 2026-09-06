"""protocol.py の単体テスト。"""
import unittest

from portal_autoapprove import protocol


class TestCursorModeMapping(unittest.TestCase):
    def test_hidden(self):
        self.assertEqual(protocol.to_mutter_cursor_mode(protocol.CURSOR_MODE_HIDDEN), 0)

    def test_embedded(self):
        self.assertEqual(protocol.to_mutter_cursor_mode(protocol.CURSOR_MODE_EMBEDDED), 1)

    def test_metadata(self):
        self.assertEqual(protocol.to_mutter_cursor_mode(protocol.CURSOR_MODE_METADATA), 2)

    def test_unknown_falls_back_to_hidden(self):
        # 未知の値は「カーソルを出さない」に倒す。勝手に映すより安全側。
        self.assertEqual(protocol.to_mutter_cursor_mode(0), 0)
        self.assertEqual(protocol.to_mutter_cursor_mode(99), 0)


class TestConstants(unittest.TestCase):
    def test_response_codes(self):
        self.assertEqual(protocol.RESPONSE_SUCCESS, 0)
        self.assertEqual(protocol.RESPONSE_CANCELLED, 1)
        self.assertEqual(protocol.RESPONSE_OTHER, 2)

    def test_source_type_monitor(self):
        self.assertEqual(protocol.SOURCE_TYPE_MONITOR, 1)


if __name__ == "__main__":
    unittest.main()
