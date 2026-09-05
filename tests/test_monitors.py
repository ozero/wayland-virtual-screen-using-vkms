"""monitors.py の単体テスト。

fixture は実機 (rg-ryzen-ubuntu-bm, 2026-09-06) の GetCurrentState から採取した実データ。
モード一覧は is-current / is-preferred を持つものだけに間引いてある。
"""
import unittest

from portal_autoapprove import monitors

VIRTUAL_SPEC = ("Virtual-1", "unknown", "unknown", "unknown")
DP1_SPEC = ("DP-1", "HPN", "HP 27f 4k", "3CM02743XR")

VIRTUAL_MONITOR = (
    VIRTUAL_SPEC,
    [
        ("3840x2160@59.993", 3840, 2160, 59.99282455444336, 1.0,
         [1.0, 2.0, 3.0, 4.0], {"is-current": True}),
        ("1024x768@60.004", 1024, 768, 60.003841400146484, 1.0,
         [1.0], {"is-preferred": True}),
    ],
    {"is-builtin": False, "display-name": "不明なディスプレイ"},
)
DP1_MONITOR = (
    DP1_SPEC,
    [
        ("3840x2160@60.000", 3840, 2160, 60.0, 1.0,
         [1.0, 2.0, 3.0, 4.0], {"is-current": True}),
        ("3840x2160@59.997", 3840, 2160, 59.99662399291992, 1.0,
         [1.0, 2.0, 3.0, 4.0], {"is-preferred": True}),
    ],
    {"is-builtin": False, "display-name": 'HP Inc. 27"'},
)
TOP_PROPS = {"renderer": "native", "layout-mode": 2, "legacy-ui-scaling-factor": 1}

# 現状: DP-1 と Virtual-1 が1つの論理モニタにまとまったミラー
MIRROR_STATE = (
    4,
    [VIRTUAL_MONITOR, DP1_MONITOR],
    [(0, 0, 1.0, 0, True, [VIRTUAL_SPEC, DP1_SPEC], {})],
    TOP_PROPS,
)

# DP-1 の電源を切った状態。connector ごと消える
VIRTUAL_ONLY_STATE = (
    5,
    [VIRTUAL_MONITOR],
    [(0, 0, 1.0, 0, True, [VIRTUAL_SPEC], {})],
    TOP_PROPS,
)

# vkms がロードされていない等で Virtual-1 が無い状態
NO_VIRTUAL_STATE = (
    6,
    [DP1_MONITOR],
    [(0, 0, 1.0, 0, True, [DP1_SPEC], {})],
    TOP_PROPS,
)

# HiDPI スケール。論理サイズは物理の 1/2 になる
SCALED_STATE = (
    7,
    [VIRTUAL_MONITOR],
    [(100, 200, 2.0, 0, True, [VIRTUAL_SPEC], {})],
    TOP_PROPS,
)

EMPTY_STATE = (8, [], [], TOP_PROPS)


class TestConnectors(unittest.TestCase):
    def test_returns_connectors_in_declaration_order(self):
        self.assertEqual(monitors.connectors(MIRROR_STATE), ["Virtual-1", "DP-1"])

    def test_empty_state_returns_empty_list(self):
        self.assertEqual(monitors.connectors(EMPTY_STATE), [])


class TestSelectConnector(unittest.TestCase):
    def test_prefers_named_connector(self):
        self.assertEqual(monitors.select_connector(MIRROR_STATE, "Virtual-1"), "Virtual-1")

    def test_prefers_named_connector_even_when_alone(self):
        self.assertEqual(
            monitors.select_connector(VIRTUAL_ONLY_STATE, "Virtual-1"), "Virtual-1")

    def test_falls_back_to_first_logical_monitor_when_preferred_missing(self):
        self.assertEqual(monitors.select_connector(NO_VIRTUAL_STATE, "Virtual-1"), "DP-1")

    def test_raises_when_no_monitor_at_all(self):
        with self.assertRaises(ValueError):
            monitors.select_connector(EMPTY_STATE, "Virtual-1")


class TestStreamGeometry(unittest.TestCase):
    def test_mirror_geometry_is_logical_monitor_origin_and_current_mode(self):
        self.assertEqual(
            monitors.stream_geometry(MIRROR_STATE, "Virtual-1"), ((0, 0), (3840, 2160)))

    def test_geometry_for_the_other_mirrored_connector_is_identical(self):
        self.assertEqual(
            monitors.stream_geometry(MIRROR_STATE, "DP-1"), ((0, 0), (3840, 2160)))

    def test_scale_divides_size_and_position_is_logical(self):
        self.assertEqual(
            monitors.stream_geometry(SCALED_STATE, "Virtual-1"), ((100, 200), (1920, 1080)))

    def test_unknown_connector_raises(self):
        with self.assertRaises(KeyError):
            monitors.stream_geometry(MIRROR_STATE, "HDMI-9")


if __name__ == "__main__":
    unittest.main()
