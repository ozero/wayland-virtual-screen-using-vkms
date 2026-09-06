"""Gio の D-Bus 呼び出しに余分な user_data が渡っていないかを構文解析で確かめる。

PyGObject では末尾に user_data を渡すとコールバックが1引数多く呼ばれ、実行時に
TypeError になる。この誤りは設置して初めて表面化するため、単体テストで塞ぐ。
このプロジェクトでは同じ誤りを3回踏んでいる。
"""
import ast
import glob
import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# メソッド名 -> user_data を含まない正しい位置引数の個数
EXPECTED_ARGS = {
    "call": 10,
    "call_sync": 9,
    "signal_subscribe": 7,
    "register_object": 5,
    "emit_signal": 5,
}


def _source_files():
    patterns = ("*.py", "portal_autoapprove/*.py", "tests/*.py")
    for pattern in patterns:
        for path in sorted(glob.glob(os.path.join(REPO, pattern))):
            yield path


def _violations():
    found = []
    for path in _source_files():
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            expected = EXPECTED_ARGS.get(node.func.attr)
            if expected is None:
                continue
            receiver = ast.unparse(node.func.value)
            if "bus" not in receiver:
                continue
            if len(node.args) > expected:
                found.append("%s:%d %s.%s() 位置引数=%d (期待=%d)" % (
                    os.path.relpath(path, REPO), node.lineno, receiver,
                    node.func.attr, len(node.args), expected))
    return found


class TestNoExtraUserData(unittest.TestCase):
    def test_no_gio_call_site_passes_user_data(self):
        violations = _violations()
        self.assertEqual(violations, [], "余分な user_data:\n  " + "\n  ".join(violations))

    def test_the_audit_actually_finds_call_sites(self):
        # 監査が空振りしていないことの確認。何も見つけられないなら守れていない。
        seen = 0
        for path in _source_files():
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), path)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in EXPECTED_ARGS
                        and "bus" in ast.unparse(node.func.value)):
                    seen += 1
        self.assertGreater(seen, 10)


if __name__ == "__main__":
    unittest.main()
