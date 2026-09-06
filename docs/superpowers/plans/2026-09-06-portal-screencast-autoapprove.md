# 画面共有ダイアログ自動承認 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** RustDesk 接続時に出る「画面を共有」ダイアログを人間の操作なしで通し、常に `Virtual-1` を共有対象にする。

**Architecture:** まず Phase 0 で `screencast-probe.py` を作り、Mutter が `Session creation inhibited` を返す条件を4状態のテスト行列で特定する。設定変更で解決すればそこで終了。解決しなければ Phase 1 として `org.freedesktop.impl.portal.ScreenCast` の自作バックエンドを実装し、`portals.conf` で差し込む。バックエンドは「ポリシー駆動プロキシ」で、条件に合う要求だけ Mutter を直接叩いて UI なしで承認し、それ以外は xdg-desktop-portal-gnome へそのまま中継する。

**Tech Stack:** Python 3.12 + `gi.repository.Gio`/`GLib`（PyGObject。既存 `mirror-apply.py` と同じ、依存追加なし）、`python3 -m unittest`（stdlib）、systemd user unit、D-Bus。

**Spec:** [../specs/2026-09-05-portal-screencast-autoapprove-design.md](../specs/2026-09-05-portal-screencast-autoapprove-design.md)

## Global Constraints

- **依存を増やさない。** 使ってよいのは Python 3 標準ライブラリと PyGObject（`gi.repository.Gio`, `GLib`）のみ。`dbus-next` / `pydbus` は未インストールで、入れない。
- **既存スクリプトの流儀に合わせる。** 実行可能な単一 CLI、docstring に日本語で用途と背景、`argparse` ではなく `sys.argv` の素朴な処理でよい（`mirror-apply.py` に倣う）。設定ファイルは持たず、すべて CLI 引数。
- **root は `.portal` の設置1回だけ。** それ以外はすべてユーザ権限で完結すること。
- **既定値**（spec 5.6 より、逐語）: `--prefer-connector Virtual-1` / `--auto-approve-when rustdesk-connected` / `--retry-seconds 10` / `--fallback-backend gnome` / `--policy-grace-ms 1500`
- **D-Bus 名**: `org.freedesktop.impl.portal.desktop.autoapprove`、オブジェクトパス `/org/freedesktop/portal/desktop`
- **公開プロパティ**（spec 5.3 より、逐語）: `AvailableSourceTypes = 1`（MONITOR のみ）、`AvailableCursorModes = 7`、`version = 5`
- **差し替えるのは `org.freedesktop.impl.portal.ScreenCast` のみ。** RemoteDesktop / FileChooser / Secret 等は GNOME のまま。
- **kill switch を壊さない。** `~/.config/xdg-desktop-portal/gnome-portals.conf` を削除して `systemctl --user restart xdg-desktop-portal` すれば必ず元の挙動に戻ること。
- **PyGObject のコールバック規約**（3.48.2 で実測確認済み。守らないと `TypeError` になる）:
  - `bus.call(..., cancellable, callback)` — 末尾に `user_data` を渡さないこと。コールバックは `(source, result)` の**2引数**
  - `bus.signal_subscribe(..., callback)` — 同上。コールバックは `(connection, sender, path, interface, signal, params)` の**6引数**
  - `bus.register_object(path, iface_info, method_call, get_property, set_property)` — `method_call` は
    `(connection, sender, path, interface, method, params, invocation)` の**7引数**、`get_property` は
    `(connection, sender, path, interface, property_name)` の**5引数**。どちらにも `user_data` も `error` も渡らない
  - `register_object` の第5引数に `None` を渡すのは正しい（set_property であって user_data ではない）
- **テスト実行**: リポジトリルートで `python3 -m unittest discover -s tests -t . -v`
- 対象環境: Ubuntu 24.04 / GNOME 46 (mutter 46.2) / Wayland / xdg-desktop-portal 1.18.4 / xdg-desktop-portal-gnome 46.2 / `XDG_CURRENT_DESKTOP=ubuntu:GNOME`

## ファイル構成

| ファイル | 責務 |
|---|---|
| `portal_autoapprove/__init__.py` | 空（パッケージ宣言のみ） |
| `portal_autoapprove/monitors.py` | **純関数**。`GetCurrentState` の unpack 結果から connector 選択とストリーム幾何を求める。Gio を import しない |
| `portal_autoapprove/protocol.py` | **純関数・定数**。ポータルと Mutter の値の対応（応答コード、source type、cursor mode）。Gio を import しない |
| `portal_autoapprove/policy.py` | **純関数**。承認するか中継するかの判定と、RustDesk 接続の検出。Gio を import しない |
| `portal_autoapprove/mutter.py` | `org.gnome.Mutter.ScreenCast` の非同期ラッパ |
| `portal_autoapprove/proxy.py` | xdg-desktop-portal-gnome への中継（メソッド / Session / Request） |
| `portal_autoapprove/impl.py` | `org.freedesktop.impl.portal.ScreenCast` の D-Bus 面とセッション状態機械 |
| `portal-autoapprove.py` | エントリポイント。引数解析と起動のみ |
| `screencast-probe.py` | Phase 0 の探針。以後は診断ツール |
| `screencast-client-test.py` | フロントエンド側から一連の流れを叩く E2E テスト |
| `portal-autoapprove-install.sh` | 設置・撤去・kill switch |
| `install/autoapprove.portal` | バックエンド宣言（root で `/usr/share/xdg-desktop-portal/portals/` へ） |
| `install/org.freedesktop.impl.portal.desktop.autoapprove.service` | D-Bus activation |
| `install/portal-autoapprove.service` | systemd user unit |
| `tests/test_monitors.py` `tests/test_protocol.py` `tests/test_policy.py` | 単体テスト |
| `docs/superpowers/findings/2026-09-06-inhibit-investigation.md` | Phase 0 の調査記録 |

純関数を3モジュールに切り出しているのは、**D-Bus を立てずに単体テストできる範囲を最大化する**ため。`monitors.py` / `protocol.py` / `policy.py` は `gi` を import してはならない。

---

# Phase 0 — 原因究明

Phase 0 の目的は「Phase 1 が必要かどうか」を決めること。設定変更で直れば Task 4 以降は実施しない。

---

### Task 1: monitors.py — connector 選択とストリーム幾何

**Files:**
- Create: `portal_autoapprove/__init__.py`
- Create: `portal_autoapprove/monitors.py`
- Test: `tests/test_monitors.py`

**Interfaces:**
- Consumes: なし（最初のタスク）
- Produces:
  - `monitors.connectors(state) -> list[str]`
  - `monitors.select_connector(state, prefer: str) -> str`
  - `monitors.stream_geometry(state, connector: str) -> tuple[tuple[int, int], tuple[int, int]]`
  - `state` は `org.gnome.Mutter.DisplayConfig.GetCurrentState` の戻り値を `GLib.Variant.unpack()` したタプル `(serial, monitors, logical_monitors, properties)`

**背景（実装者向け）:** `GetCurrentState` のシグネチャは
`(u serial, a((ssss)a(siiddada{sv})a{sv}) monitors, a(iiduba(ssss)a{sv}) logical_monitors, a{sv} properties)`。
- `monitors` の各要素は `((connector, vendor, product, serial), modes, properties)`
- `modes` の各要素は `(id, width, height, refresh, preferred_scale, supported_scales, properties)` の7要素タプル。`properties` に `is-current: True` があるものが現在のモード
- `logical_monitors` の各要素は `(x, y, scale, transform, primary, [monitor_spec, ...], properties)`
- ミラーの場合、1つの論理モニタが複数の monitor_spec を持つ

- [ ] **Step 1: パッケージを作る**

```bash
mkdir -p portal_autoapprove tests
touch portal_autoapprove/__init__.py
```

- [ ] **Step 2: 失敗するテストを書く**

`tests/test_monitors.py`:

```python
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
```

- [ ] **Step 3: テストが失敗することを確認**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'portal_autoapprove.monitors'`

- [ ] **Step 4: monitors.py を実装**

`portal_autoapprove/monitors.py`:

```python
"""Mutter の DisplayConfig 状態から、記録すべき connector と幾何を決める。

ここは純関数だけを置く。gi を import しないこと(単体テストを D-Bus 無しで回すため)。

state は org.gnome.Mutter.DisplayConfig.GetCurrentState の戻り値を unpack した
  (serial, monitors, logical_monitors, properties)
であり、
  monitors[i]         = ((connector, vendor, product, serial), modes, properties)
  modes[j]            = (id, width, height, refresh, preferred_scale, supported_scales, props)
  logical_monitors[k] = (x, y, scale, transform, primary, [monitor_spec, ...], properties)
という形をしている。
"""


def connectors(state):
    """connector 名を monitors の宣言順で返す。"""
    _, monitors, _, _ = state
    return [spec[0] for spec, _modes, _props in monitors]


def select_connector(state, prefer):
    """記録対象の connector 名を1つ選ぶ。

    prefer が接続中ならそれを返す。無ければ最初の論理モニタが持つ最初の connector。
    どちらも取れなければ ValueError。

    prefer を名前で固定するのは、物理モニタが電源 OFF で connector ごと消えるのに対し
    VKMS の仮想モニタは常に connected だから。先頭を機械的に選ぶとミラーが崩れている
    ときに消えるモニタを掴む。
    """
    names = connectors(state)
    if prefer in names:
        return prefer

    _, _, logical_monitors, _ = state
    for _x, _y, _scale, _transform, _primary, specs, _props in logical_monitors:
        if specs:
            return specs[0][0]

    if names:
        return names[0]

    raise ValueError("接続中のモニタが1つも無い")


def _find_monitor(state, connector):
    _, monitors, _, _ = state
    for spec, modes, props in monitors:
        if spec[0] == connector:
            return spec, modes, props
    raise KeyError(connector)


def _current_mode(modes, connector):
    for mode in modes:
        if mode[6].get("is-current"):
            return mode
    raise KeyError("%s に is-current なモードが無い" % connector)


def _logical_monitor_for(state, connector):
    _, _, logical_monitors, _ = state
    for lm in logical_monitors:
        specs = lm[5]
        if any(spec[0] == connector for spec in specs):
            return lm
    raise KeyError("%s を含む論理モニタが無い" % connector)


def stream_geometry(state, connector):
    """(position, size) を返す。

    position は connector を含む論理モニタの (x, y)。
    size は現在モードの (width, height) を論理モニタの scale で割った論理サイズ。
    connector が見つからなければ KeyError。
    """
    _spec, modes, _props = _find_monitor(state, connector)
    mode = _current_mode(modes, connector)
    width, height = mode[1], mode[2]

    lm = _logical_monitor_for(state, connector)
    x, y, scale = lm[0], lm[1], lm[2]

    return (x, y), (int(width / scale), int(height / scale))
```

- [ ] **Step 5: テストが通ることを確認**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS — 10 tests

- [ ] **Step 6: コミット**

```bash
git add portal_autoapprove/__init__.py portal_autoapprove/monitors.py tests/test_monitors.py
git commit -m "monitors: GetCurrentState から記録対象 connector と幾何を決める純関数を追加"
```

---

### Task 2: protocol.py + mutter.py + screencast-probe.py — Mutter 直接探針

**Files:**
- Create: `portal_autoapprove/protocol.py`
- Create: `portal_autoapprove/mutter.py`
- Create: `screencast-probe.py`
- Test: `tests/test_protocol.py`

**Interfaces:**
- Consumes: `monitors.select_connector`, `monitors.stream_geometry`（Task 1）
- Produces:
  - `protocol.RESPONSE_SUCCESS = 0` / `RESPONSE_CANCELLED = 1` / `RESPONSE_OTHER = 2`
  - `protocol.SOURCE_TYPE_MONITOR = 1`
  - `protocol.to_mutter_cursor_mode(portal_cursor_mode: int) -> int`
  - `mutter.InhibitedError`（`mutter.ScreenCastError` のサブクラス）
  - `mutter.get_current_state(bus) -> tuple`
  - `mutter.record_monitor(bus, connector, cursor_mode, on_ready, on_error, timeout_ms=5000) -> None`
  - `mutter.Recording`（属性 `node_id: int`、メソッド `stop()`, `connect_closed(cb)`）

**背景（実装者向け）— 必ず非同期で書くこと:**
`record_monitor` は `CreateSession` → `RecordMonitor` → (`PipeWireStreamAdded` を購読) → `Start` という
往復をする。**同期呼び出し（`bus.call_sync`）で書くと `PipeWireStreamAdded` が同じ
メインループに届くためデッドロックする。** 必ず `bus.call`（非同期）とコールバックで組むこと。

ポータルと Mutter で cursor mode の値が違う:

| | HIDDEN | EMBEDDED | METADATA |
|---|---|---|---|
| ポータル (`org.freedesktop.portal.ScreenCast`) | 1 | 2 | 4 |
| Mutter (`RecordMonitor` の `cursor-mode`) | 0 | 1 | 2 |

- [ ] **Step 1: protocol.py の失敗するテストを書く**

`tests/test_protocol.py`:

```python
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
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'portal_autoapprove.protocol'`

- [ ] **Step 3: protocol.py を実装**

`portal_autoapprove/protocol.py`:

```python
"""ポータルと Mutter の間の値の対応。

ここは純関数と定数だけを置く。gi を import しないこと。
"""

# org.freedesktop.impl.portal.Request の応答コード
RESPONSE_SUCCESS = 0
RESPONSE_CANCELLED = 1
RESPONSE_OTHER = 2

# org.freedesktop.portal.ScreenCast の SourceType
SOURCE_TYPE_MONITOR = 1
SOURCE_TYPE_WINDOW = 2
SOURCE_TYPE_VIRTUAL = 4

# org.freedesktop.portal.ScreenCast の CursorMode
CURSOR_MODE_HIDDEN = 1
CURSOR_MODE_EMBEDDED = 2
CURSOR_MODE_METADATA = 4

# Mutter の RecordMonitor が取る cursor-mode。ポータルと値が違うので変換が要る。
_MUTTER_CURSOR_MODE = {
    CURSOR_MODE_HIDDEN: 0,
    CURSOR_MODE_EMBEDDED: 1,
    CURSOR_MODE_METADATA: 2,
}


def to_mutter_cursor_mode(portal_cursor_mode):
    """ポータルの CursorMode を Mutter の cursor-mode に変換する。

    未知の値は 0(hidden) に倒す。勝手にカーソルを映すより安全側。
    """
    return _MUTTER_CURSOR_MODE.get(portal_cursor_mode, 0)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS — 16 tests

- [ ] **Step 5: mutter.py を実装**

`portal_autoapprove/mutter.py`:

```python
"""org.gnome.Mutter.ScreenCast の非同期ラッパ。

同期呼び出しで書くとデッドロックする。CreateSession -> RecordMonitor -> Start の
往復の途中で PipeWireStreamAdded シグナルを同じメインループで受け取る必要があるため。
すべて bus.call() の非同期版で組んである。
"""
from gi.repository import Gio, GLib

from portal_autoapprove import protocol

SCREEN_CAST_NAME = "org.gnome.Mutter.ScreenCast"
SCREEN_CAST_PATH = "/org/gnome/Mutter/ScreenCast"
SCREEN_CAST_IFACE = "org.gnome.Mutter.ScreenCast"
SESSION_IFACE = "org.gnome.Mutter.ScreenCast.Session"
STREAM_IFACE = "org.gnome.Mutter.ScreenCast.Stream"

DISPLAY_CONFIG_NAME = "org.gnome.Mutter.DisplayConfig"
DISPLAY_CONFIG_PATH = "/org/gnome/Mutter/DisplayConfig"
DISPLAY_CONFIG_IFACE = "org.gnome.Mutter.DisplayConfig"

# Mutter が gnome-shell 側の inhibit で断ってくるときの文字列。
# libmutter-14.so.0 の "Session creation inhibited" に対応する。
_INHIBITED_MARKER = "Session creation inhibited"


class ScreenCastError(Exception):
    """Mutter の ScreenCast 操作の失敗。"""


class InhibitedError(ScreenCastError):
    """gnome-shell 側が remote access を inhibit しているためセッションを作れない。"""


def _wrap_error(error):
    if error is not None and _INHIBITED_MARKER in error.message:
        return InhibitedError(error.message)
    return ScreenCastError(error.message if error else "不明なエラー")


def get_current_state(bus):
    """DisplayConfig.GetCurrentState の結果を unpack して返す。

    monitors.py に渡すための同期呼び出し。シグナル待ちを含まないので安全。
    """
    result = bus.call_sync(
        DISPLAY_CONFIG_NAME, DISPLAY_CONFIG_PATH, DISPLAY_CONFIG_IFACE,
        "GetCurrentState", None, None, Gio.DBusCallFlags.NONE, -1, None)
    return result.unpack()


class Recording:
    """Mutter の ScreenCast セッション1本と、その中の1ストリーム。"""

    def __init__(self, bus, session_path, stream_path, node_id):
        self._bus = bus
        self._session_path = session_path
        self._stream_path = stream_path
        self._closed_subscription = None
        self.node_id = node_id

    def connect_closed(self, callback):
        """Mutter 側でセッションが閉じられたときに callback() を呼ぶ。"""
        self._closed_subscription = self._bus.signal_subscribe(
            SCREEN_CAST_NAME, SESSION_IFACE, "Closed", self._session_path, None,
            Gio.DBusSignalFlags.NONE,
            lambda *_args: callback())

    def stop(self):
        """セッションを閉じる。二重呼び出しは無害。"""
        if self._closed_subscription is not None:
            self._bus.signal_unsubscribe(self._closed_subscription)
            self._closed_subscription = None
        if self._session_path is None:
            return
        path, self._session_path = self._session_path, None
        self._bus.call(
            SCREEN_CAST_NAME, path, SESSION_IFACE, "Stop", None, None,
            Gio.DBusCallFlags.NONE, -1, None, None)


def record_monitor(bus, connector, cursor_mode, on_ready, on_error, timeout_ms=5000):
    """指定 connector の記録を開始し、node_id が取れたら on_ready(Recording) を呼ぶ。

    cursor_mode はポータル側の値(1/2/4)を渡すこと。内部で Mutter の値に変換する。
    失敗時は on_error(Exception) を呼ぶ。inhibit なら InhibitedError が渡る。
    GLib のメインループが回っていることが前提。
    """
    state = {"done": False, "aborted": False, "session_path": None,
             "stream_path": None, "subscription": None, "timeout_id": None}

    def finish_error(exc):
        if state["done"]:
            return
        state["done"] = True
        state["aborted"] = True
        _cleanup(bus, state)
        on_error(exc)

    def abandon(session_path=None):
        """打ち切ったあとに遅れて届いた応答の後始末。

        タイムアウトやエラーで打ち切ったあとに Mutter の応答が届くと、こちらが
        知らないままセッションが走り続ける(=「画面共有中」の表示が消えない)。
        常駐デーモンではプロセス終了による後始末も効かないので、遅れて判明した
        セッションはここで明示的に止める。
        """
        path = session_path or state["session_path"]
        if state["subscription"] is not None:
            bus.signal_unsubscribe(state["subscription"])
            state["subscription"] = None
        if path is not None:
            bus.call(SCREEN_CAST_NAME, path, SESSION_IFACE, "Stop", None, None,
                     Gio.DBusCallFlags.NONE, -1, None, None)
        state["session_path"] = None

    def finish_ok(node_id):
        if state["done"]:
            return
        state["done"] = True
        if state["timeout_id"] is not None:
            GLib.source_remove(state["timeout_id"])
            state["timeout_id"] = None
        if state["subscription"] is not None:
            bus.signal_unsubscribe(state["subscription"])
            state["subscription"] = None
        on_ready(Recording(bus, state["session_path"], state["stream_path"], node_id))

    def on_timeout():
        state["timeout_id"] = None
        finish_error(ScreenCastError(
            "PipeWireStreamAdded が %dms 以内に来なかった" % timeout_ms))
        return GLib.SOURCE_REMOVE

    def on_stream_added(_conn, _sender, _path, _iface, _signal, params):
        finish_ok(params.unpack()[0])

    def on_start_done(_source, res):
        try:
            bus.call_finish(res)
        except GLib.Error as err:
            finish_error(_wrap_error(err))
            return
        if state["aborted"]:
            abandon()

    def on_record_done(_source, res):
        try:
            reply = bus.call_finish(res)
        except GLib.Error as err:
            finish_error(_wrap_error(err))
            return
        if state["aborted"]:
            abandon()
            return
        state["stream_path"] = reply.unpack()[0]
        # Start より先に購読する。順序を逆にすると取りこぼす。
        state["subscription"] = bus.signal_subscribe(
            SCREEN_CAST_NAME, STREAM_IFACE, "PipeWireStreamAdded",
            state["stream_path"], None, Gio.DBusSignalFlags.NONE,
            on_stream_added)
        bus.call(SCREEN_CAST_NAME, state["session_path"], SESSION_IFACE,
                 "Start", None, None, Gio.DBusCallFlags.NONE, -1, None,
                 on_start_done)

    def on_create_done(_source, res):
        try:
            reply = bus.call_finish(res)
        except GLib.Error as err:
            finish_error(_wrap_error(err))
            return
        session_path = reply.unpack()[0]
        if state["aborted"]:
            # 打ち切った後に作られたセッション。放置すると誰も止められない。
            abandon(session_path)
            return
        state["session_path"] = session_path
        props = {"cursor-mode": GLib.Variant("u", protocol.to_mutter_cursor_mode(cursor_mode))}
        bus.call(SCREEN_CAST_NAME, state["session_path"], SESSION_IFACE,
                 "RecordMonitor", GLib.Variant("(sa{sv})", (connector, props)),
                 GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, -1, None,
                 on_record_done)

    state["timeout_id"] = GLib.timeout_add(timeout_ms, on_timeout)
    bus.call(SCREEN_CAST_NAME, SCREEN_CAST_PATH, SCREEN_CAST_IFACE,
             "CreateSession", GLib.Variant("(a{sv})", ({},)),
             GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, -1, None,
             on_create_done)


def _cleanup(bus, state):
    if state["timeout_id"] is not None:
        GLib.source_remove(state["timeout_id"])
        state["timeout_id"] = None
    if state["subscription"] is not None:
        bus.signal_unsubscribe(state["subscription"])
        state["subscription"] = None
    if state["session_path"] is not None:
        bus.call(SCREEN_CAST_NAME, state["session_path"], SESSION_IFACE,
                 "Stop", None, None, Gio.DBusCallFlags.NONE, -1, None, None)
        state["session_path"] = None
```

- [ ] **Step 5b: mutter.py の状態機械テストを書く**

`record_monitor` は「タイムアウト」「3つの D-Bus 応答」「PipeWire のシグナル」が任意の順序で
届く状態機械で、順序の取り違えは**止められない録画セッションの取り残し**（=「画面共有中」の
表示が消えない）に直結する。にせの `bus` と差し替えた `GLib.timeout_add` で、D-Bus を立てずに
決定的に再現できるので、レースの経路に絞って固定する。

`tests/test_mutter.py`:

```python
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

    def test_stream_signal_before_start_reply_keeps_the_recording_alive(self):
        """PipeWireStreamAdded が Start の応答より先に届く順序。

        購読を Start より先に行っている以上この順序は起こりうる。成功したあとに
        Start の応答が届いても、渡したばかりのセッションを止めてはいけない。
        """
        self.start()
        self.bus.find("CreateSession")[0]["callback"](
            None, self.reply_object("/session/u1"))
        self.bus.find("RecordMonitor")[0]["callback"](
            None, self.reply_object("/stream/u1"))
        sub = next(iter(self.bus.subscriptions.values()))

        # Start の応答より先にシグナルが届く
        sub["callback"](None, None, "/stream/u1", None, "PipeWireStreamAdded",
                        GLib.Variant("(u)", (75,)))
        self.assertEqual(len(self.ready), 1)
        self.assertEqual(self.ready[0].node_id, 75)

        # そのあとに Start の成功応答が届いても Stop してはいけない
        self.bus.find("Start")[0]["callback"](None, None)
        self.assertNotIn("Stop", self.bus.methods())
        self.assertEqual(self.errors, [])


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
```

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS — 21 tests（Task 1 の 10 + protocol の 6 + mutter の 5）

- [ ] **Step 6: screencast-probe.py を実装**

`screencast-probe.py`:

```python
#!/usr/bin/env python3
"""Mutter の ScreenCast にセッションを作れるかを直接確かめる探針。

「画面を共有」ダイアログを自動承認しても、Mutter が
  Session creation inhibited
を返す状態では結局セッションが作れない。ポータル層に手を入れる前に、
まず Mutter 単体で通るかどうかをここで確かめる。

  python3 screencast-probe.py                       # Virtual-1 を記録して即停止
  python3 screencast-probe.py --connector DP-1      # 対象を変える
  python3 screencast-probe.py --keep                # Ctrl-C まで記録し続ける
  python3 screencast-probe.py --show                # モニタ構成だけ表示

副作用は「画面共有中」インジケータが数秒出るだけ。--keep でなければ自動で止まる。
"""
import sys

from gi.repository import Gio, GLib

from portal_autoapprove import monitors, mutter, protocol


def show(state):
    print("connectors:", ", ".join(monitors.connectors(state)))
    for name in monitors.connectors(state):
        pos, size = monitors.stream_geometry(state, name)
        print("  %-12s position=%s size=%dx%d" % (name, pos, size[0], size[1]))


def main(argv):
    connector = None
    keep = False
    for i, arg in enumerate(argv):
        if arg == "--connector":
            connector = argv[i + 1]
        elif arg == "--keep":
            keep = True
        elif arg in ("-h", "--help"):
            print(__doc__)
            return 0

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    state = mutter.get_current_state(bus)

    if "--show" in argv:
        show(state)
        return 0

    if connector is None:
        connector = monitors.select_connector(state, "Virtual-1")
    pos, size = monitors.stream_geometry(state, connector)
    print("記録対象: %s position=%s size=%dx%d" % (connector, pos, size[0], size[1]))

    loop = GLib.MainLoop()
    status = {"code": 1}

    def on_ready(recording):
        print("成功: node_id=%d" % recording.node_id)
        print("→ Mutter 側は健全。ポータル層の対策(Phase 1)が成立する。")
        status["code"] = 0
        if keep:
            print("--keep 指定。Ctrl-C で停止する。")
            return
        recording.stop()
        loop.quit()

    def on_error(exc):
        if isinstance(exc, mutter.InhibitedError):
            print("失敗: セッション作成が inhibit されている")
            print("  %s" % exc)
            print("→ この状態ではポータル層に手を入れても解決しない。")
            print("  gnome-shell 側の inhibit 要因を先に特定すること。")
            status["code"] = 2
        else:
            print("失敗: %s" % exc)
            status["code"] = 1
        loop.quit()

    mutter.record_monitor(bus, connector, protocol.CURSOR_MODE_EMBEDDED,
                          on_ready, on_error)
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\n中断")
    return status["code"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 7: 実機で探針を動かす**

```bash
chmod +x screencast-probe.py
python3 screencast-probe.py --show
python3 screencast-probe.py
```

Expected: `--show` は `Virtual-1` と `DP-1` の両方を position=(0, 0) size=3840x2160 で表示する（ミラーなので同一）。
本実行は `成功: node_id=<数字>`（Mutter が健全）か `失敗: セッション作成が inhibit されている`（層1が塞がっている）のどちらか。**どちらでもこのタスクは成功**。結果は Task 3 で記録する。

- [ ] **Step 8: コミット**

```bash
git add portal_autoapprove/protocol.py portal_autoapprove/mutter.py \
        screencast-probe.py tests/test_protocol.py tests/test_mutter.py
git commit -m "screencast-probe: Mutter の ScreenCast に直接セッションを張る探針を追加"
```

---

### Task 3: inhibit 要因の特定と app_id 検証（調査タスク）

**Files:**
- Create: `docs/superpowers/findings/2026-09-06-inhibit-investigation.md`

**Interfaces:**
- Consumes: `screencast-probe.py`（Task 2）
- Produces: 調査記録と、Phase 1 に進むかどうかの判定

**注意:** これはコードを書くタスクではないので TDD は適用しない。各ステップは
「操作して結果を記録する」もの。**S1 は物理モニタの電源ボタンを押す必要があるため、
実行には人間の立ち会いが要る。**

- [ ] **Step 1: 記録ファイルの雛形を作る**

`docs/superpowers/findings/2026-09-06-inhibit-investigation.md`:

```markdown
# inhibit 要因の調査記録

`screencast-probe.py` を4状態で実行し、Mutter が `Session creation inhibited` を
返す条件を特定する。設計書 4.2 に対応。

## 結果

| 状態 | 作り方 | probe の結果 | 備考 |
|---|---|---|---|
| S0 通常 | 何もしない | | |
| S1 DP-1 電源 OFF | モニタの電源ボタン | | |
| S2 アイドルブランク後 | `idle-delay` を 60 にして1分放置 | | |
| S3 S1 + S2 | 電源 OFF のまま1分放置 | | |

## app_id 検証 (P0-3)

| 確認項目 | 結果 |
|---|---|
| journal に `Assigning app ID` が出るか | |
| `flatpak permissions screencast` にエントリが増えるか | |
| 2回目の接続でダイアログが出ないか | |

## 結論

（Phase 1 に進むか、設定変更で解決したかを書く）
```

- [ ] **Step 2: S0（通常状態）で探針を実行**

```bash
python3 screencast-probe.py; echo "exit=$?"
```

Expected: `exit=0`（成功）なら inhibit は間欠的。`exit=2` なら**恒久的な inhibit** で、
Phase 1 を含む全案が成立しない。結果を記録ファイルの S0 行に書く。

- [ ] **Step 3: S1（DP-1 電源 OFF）で探針を実行**

物理モニタの電源ボタンを押して消灯させてから:

```bash
python3 screencast-probe.py --show     # Virtual-1 だけになっているか確認
python3 screencast-probe.py; echo "exit=$?"
```

Expected: `--show` が `connectors: Virtual-1` のみを表示すること。探針の結果を S1 行に記録。
モニタを再点灯して元に戻す。

- [ ] **Step 4: S2（アイドルブランク）で探針を実行**

```bash
gsettings get org.gnome.desktop.session idle-delay        # 元の値(900)を控える
gsettings set org.gnome.desktop.session idle-delay 60
```

1分以上放置して画面がブランクしたら、リモートまたは別端末から:

```bash
python3 screencast-probe.py; echo "exit=$?"
```

Expected: 結果を S2 行に記録。S0 が成功で S2 が `exit=2` なら**アイドルブランクが原因**で、
`gsettings set org.gnome.desktop.session idle-delay 0` が対処になる。

- [ ] **Step 5: S3（電源 OFF + ブランク）で探針を実行**

モニタを消灯したまま1分以上放置してから探針を実行し、S3 行に記録。
終わったら `gsettings set org.gnome.desktop.session idle-delay 900` で元に戻す
（S2 が原因と判明した場合は `0` にする）。

- [ ] **Step 6: gnome-shell 側の inhibit 呼び出し元を特定**

```bash
# gnome-shell の JS リソースの所在を探す
for f in /usr/bin/gnome-shell /usr/lib/x86_64-linux-gnu/gnome-shell/*.so \
         /usr/share/gnome-shell/*.gresource; do
  [ -e "$f" ] || continue
  n=$(gresource list "$f" 2>/dev/null | grep -c '\.js$')
  [ "$n" -gt 0 ] && echo "$f: $n js"
done
```

見つかったリソースから `inhibit_remote_access` を含む JS を抽出して呼び出し元を記録する:

```bash
RES=<上で見つかったファイル>
for js in $(gresource list "$RES" | grep '\.js$'); do
  gresource extract "$RES" "$js" 2>/dev/null | grep -q inhibit_remote_access && echo "$js"
done
```

Expected: 呼び出し元の JS ファイル名（例 `/org/gnome/shell/ui/screenShield.js`）を記録ファイルに書く。
見つからなければ「特定できず」と書く。それでも S0〜S3 の結果があれば判断はできる。

- [ ] **Step 7: P0-3（app_id 付与）を検証**

```bash
journalctl --user -f -u xdg-desktop-portal &     # 別端末で監視
systemd-run --user --scope --unit=app-rustdesk-probe \
  /usr/share/rustdesk/rustdesk --server
```

その状態で RustDesk 接続を試み、以下を確認して記録:

```bash
journalctl --user -u xdg-desktop-portal --since "5 min ago" | grep "Assigning app ID"
flatpak permissions screencast
```

Expected: `Assigning app ID "rustdesk" to pid ... which has unit "app-rustdesk-probe.scope"`
が出れば app_id 付与に成功。その後 `flatpak permissions screencast` に `rustdesk` の
エントリが増え、**2回目の接続でダイアログが出なければ P0-3 で解決**。

- [ ] **Step 8: 結論を書いてコミット**

設計書 4.4 の出口表に照らして結論を書く。

```bash
git add docs/superpowers/findings/2026-09-06-inhibit-investigation.md
git commit -m "findings: inhibit 要因の調査結果を記録"
```

---

## ゲート: Phase 1 に進むか

Task 3 の結論に従う（設計書 4.4）。

| S0 の結果 | P0-1 / P0-3 | 次にやること |
|---|---|---|
| 成功 | どちらかで解決 | **ここで終了。** 設定を恒久化して README を更新し、Task 4 以降は実施しない |
| 成功 | どちらでも解決しない | **Task 4 へ進む** |
| inhibit | 要因が判明し解除できた | 解除した状態で Task 3 Step 2 からやり直す |
| inhibit | 要因不明 | **Task 4 へ進んではいけない。** Mutter が断る以上バックエンドを作っても動かない。gnome-shell 側の発生源特定に切り替えること |

---

# Phase 1 — 自作 ScreenCast バックエンド

**ゲートを通過した場合のみ実施する。**

---

### Task 4: policy.py — 承認するか中継するかの判定

**Files:**
- Create: `portal_autoapprove/policy.py`
- Test: `tests/test_policy.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `policy.Decision`（namedtuple、フィールド `approve: bool`, `reason: str`）
  - `policy.MODE_RUSTDESK_CONNECTED = "rustdesk-connected"` / `MODE_ALWAYS = "always"` / `MODE_NEVER = "never"`
  - `policy.MODES`（上記3つのタプル）
  - `policy.decide(mode: str, rustdesk_connected: bool) -> Decision`
  - `policy.is_rustdesk_connected(proc_root: str = "/proc") -> bool`

**背景（実装者向け）:** ポータルの impl backend からは要求元プロセスを同定できない
（呼び出し元は `xdg-desktop-portal` 自身で、`app_id` は非サンドボックスのアプリでは
空文字列。設計書 2.6）。そこで「RustDesk が接続中か」を代理指標にする。RustDesk は
接続を受けたときだけ `rustdesk --cm`（Connection Manager）を起動する。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_policy.py`:

```python
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

    def test_ignores_unrelated_binary_with_cm_and_rustdesk_in_an_argument(self):
        # rustdesk という名前のディレクトリを --cm 付きで扱う無関係なコマンド。
        # 引数への部分一致で通すと、画面キャプチャを無言で承認してしまう。
        self._add_process(105, ["/usr/bin/somecmd", "--cm",
                                "/home/user/projects/rustdesk/notes.txt"])
        self.assertFalse(policy.is_rustdesk_connected(self.root))

    def test_ignores_shell_command_mentioning_rustdesk_cm(self):
        self._add_process(106, ["/bin/bash", "-c", "pgrep -a -f 'rustdesk --cm'"])
        self.assertFalse(policy.is_rustdesk_connected(self.root))

    def test_accepts_bare_executable_name(self):
        self._add_process(107, ["rustdesk", "--cm"])
        self.assertTrue(policy.is_rustdesk_connected(self.root))

    def test_ignores_empty_cmdline(self):
        d = os.path.join(self.root, "108")
        os.makedirs(d)
        with open(os.path.join(d, "cmdline"), "wb") as handle:
            handle.write(b"")
        self.assertFalse(policy.is_rustdesk_connected(self.root))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'portal_autoapprove.policy'`

- [ ] **Step 3: policy.py を実装**

`portal_autoapprove/policy.py`:

```python
"""画面キャプチャ要求を自動承認するか、GNOME のバックエンドへ中継するかの判定。

ここは純関数だけを置く。gi を import しないこと。

ポータルの impl backend からは要求元プロセスを同定できない。呼び出し元は
xdg-desktop-portal 自身であり、app_id は非サンドボックスのアプリでは空文字列に
なるため(設計書 2.6)。そこで「RustDesk が接続中か」を代理指標として使う。
"""
import collections
import os

Decision = collections.namedtuple("Decision", "approve reason")

MODE_RUSTDESK_CONNECTED = "rustdesk-connected"
MODE_ALWAYS = "always"
MODE_NEVER = "never"
MODES = (MODE_RUSTDESK_CONNECTED, MODE_ALWAYS, MODE_NEVER)


def decide(mode, rustdesk_connected):
    """承認するか中継するかを決める。

    approve=False は「拒否」ではなく「GNOME のバックエンドへ中継」を意味する。
    つまり従来どおりダイアログが出る。
    """
    if mode == MODE_NEVER:
        return Decision(False, "mode-never")
    if mode == MODE_ALWAYS:
        return Decision(True, "mode-always")
    if mode == MODE_RUSTDESK_CONNECTED:
        if rustdesk_connected:
            return Decision(True, "rustdesk-cm-running")
        return Decision(False, "rustdesk-not-connected")
    raise ValueError("未知のモード: %r" % (mode,))


def is_rustdesk_connected(proc_root="/proc"):
    """rustdesk の Connection Manager (--cm) が動いていれば True。

    RustDesk は接続を受けたときだけ `rustdesk --cm` を起動する。
    proc_root はテストで差し替えるためのもの。

    この関数の True は「画面キャプチャを無言で承認してよい」を意味するため、
    誤検知は安全上の欠陥になる。実行ファイル名を argv[0] の basename で厳密に
    照合し、引数のどこかに rustdesk という文字列が現れるだけのプロセス
    (rustdesk という名前のディレクトリを扱う無関係なコマンド等) は弾く。
    """
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return False

    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, entry, "cmdline"), "rb") as handle:
                argv = handle.read().split(b"\0")
        except OSError:
            continue  # 読んでいる間に消えたプロセス
        if not argv or not argv[0]:
            continue
        if os.path.basename(argv[0]) != b"rustdesk":
            continue
        if b"--cm" in argv:
            return True
    return False
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS — 35 tests（Task 1-2 の 21 + policy の 14）

- [ ] **Step 5: 実機で `--cm` の検出を確認**

RustDesk で接続していない状態と、接続中の状態でそれぞれ:

```bash
python3 -c "from portal_autoapprove import policy; print(policy.is_rustdesk_connected())"
```

Expected: 未接続で `False`、接続中に `True`。

**実測済みの背景（2026-09-06）:** RustDesk の `--cm` は ScreenCast 要求と**同じ秒**に起動する。

```
14:04:31  Activating ... org.freedesktop.impl.portal.desktop.gnome   ← ScreenCast 要求
14:04:31  rustdesk[3420107]: flutter: --cm started
```

秒未満の順序は不明なので、`CreateSession` の時点で `--cm` がまだ現れていない取りこぼしが
起こりうる。これは Task 6 の猶予ポーリング（`--policy-grace-ms`）で塞ぐ。

代替案として「`rustdesk` プロセスが ESTABLISHED な TCP 接続を持つか」も実測したが、
**`rustdesk --server` は未接続でも中継サーバへの接続を張りっぱなし**（`ESTABLISHED=2`）で
`always` と区別がつかないため不採用。

- [ ] **Step 6: コミット**

```bash
git add portal_autoapprove/policy.py tests/test_policy.py
git commit -m "policy: 自動承認するか GNOME へ中継するかの判定を追加"
```

---

### Task 5: 中継専用バックエンド — 差し込んでも挙動が変わらないことを確かめる

**Files:**
- Create: `portal_autoapprove/proxy.py`
- Create: `portal_autoapprove/impl.py`
- Create: `portal-autoapprove.py`
- Create: `install/autoapprove.portal`
- Create: `portal-autoapprove-install.sh`

**Interfaces:**
- Consumes: なし（この段階では policy も mutter も使わない）
- Produces:
  - `proxy.Backend(bus, name)`、メソッド `forward(method, params, invocation)`, `close_object(path, iface)`, `subscribe_closed(session_path, callback) -> int`, `unsubscribe(subscription_id)`
  - `proxy.PORTAL_PATH`, `proxy.SCREEN_CAST_IFACE`, `proxy.SESSION_IFACE`, `proxy.REQUEST_IFACE`, `proxy.REPLY_TYPE`
  - `impl.ScreenCastBackend(bus, fallback_backend_name)`、メソッド `register() -> None`
  - `impl.BUS_NAME`, `impl.PORTAL_PATH`

**このタスクの狙い:** 承認ロジックを一切入れず、**すべての要求を xdg-desktop-portal-gnome へ
中継するだけ**のバックエンドを作って差し込む。これで「ダイアログが今までどおり出る」なら、
D-Bus の配管とバックエンド差し替えが正しく効いていることが確認できる。承認パスは Task 6 で足す。

- [ ] **Step 1: proxy.py を実装**

`portal_autoapprove/proxy.py`:

```python
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
```

- [ ] **Step 2: impl.py を実装（中継のみ）**

`portal_autoapprove/impl.py`:

```python
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
```

- [ ] **Step 3: エントリポイントを実装**

`portal-autoapprove.py`:

```python
#!/usr/bin/env python3
"""画面共有(ScreenCast)ポータルの自作バックエンド。

RustDesk 接続中の画面キャプチャ要求だけを UI 無しで承認し、それ以外は
xdg-desktop-portal-gnome へそのまま中継する。中継するので「承認しない要求」は
拒否ではなく従来どおりダイアログになる。

  --prefer-connector NAME    記録する connector 名 (既定 Virtual-1)
  --auto-approve-when MODE   rustdesk-connected | always | never (既定 rustdesk-connected)
  --retry-seconds N          Mutter の inhibit に対するリトライ上限 (既定 10)
  --fallback-backend NAME    中継先 (既定 gnome)
  --policy-grace-ms N        rustdesk --cm の出現を待つ猶予 (既定 1500)

停止して元の挙動に戻すには ~/.config/xdg-desktop-portal/gnome-portals.conf を消して
systemctl --user restart xdg-desktop-portal すればよい。
"""
import logging
import sys

from gi.repository import Gio, GLib

from portal_autoapprove import impl, policy

DEFAULTS = {
    "--prefer-connector": "Virtual-1",
    "--auto-approve-when": policy.MODE_RUSTDESK_CONNECTED,
    "--retry-seconds": "10",
    "--fallback-backend": "gnome",
    "--policy-grace-ms": "1500",
}


def parse_args(argv):
    opts = dict(DEFAULTS)
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(__doc__)
            raise SystemExit(0)
        if arg not in DEFAULTS:
            raise SystemExit("未知の引数: %s" % arg)
        if i + 1 >= len(argv):
            raise SystemExit("%s に値がない" % arg)
        opts[arg] = argv[i + 1]
        i += 2
    if opts["--auto-approve-when"] not in policy.MODES:
        raise SystemExit("--auto-approve-when は %s のいずれか"
                         % " | ".join(policy.MODES))
    return opts


def main(argv):
    opts = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        stream=sys.stderr)
    log = logging.getLogger("portal-autoapprove")

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    backend = impl.ScreenCastBackend(
        bus, "org.freedesktop.impl.portal.desktop.%s" % opts["--fallback-backend"])

    loop = GLib.MainLoop()

    def on_name_acquired(*_args):
        backend.register()

    def on_name_lost(*_args):
        log.error("D-Bus 名 %s を取得できなかった", impl.BUS_NAME)
        loop.quit()

    Gio.bus_own_name(Gio.BusType.SESSION, impl.BUS_NAME,
                     Gio.BusNameOwnerFlags.NONE, None,
                     on_name_acquired, on_name_lost)
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: `.portal` ファイルを作る**

`install/autoapprove.portal`:

```
[portal]
DBusName=org.freedesktop.impl.portal.desktop.autoapprove
Interfaces=org.freedesktop.impl.portal.ScreenCast;
```

- [ ] **Step 5: 設置スクリプトを書く**

`portal-autoapprove-install.sh`:

```bash
#!/usr/bin/env bash
# ScreenCast ポータルの自作バックエンドを設置/撤去する。
#
#   bash portal-autoapprove-install.sh --install [--mode MODE]
#   bash portal-autoapprove-install.sh --uninstall
#   bash portal-autoapprove-install.sh --status
#
# --install は .portal の設置に sudo を1回だけ使う。それ以外はユーザ権限で完結する。
# MODE は rustdesk-connected(既定) | always | never。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME=org.freedesktop.impl.portal.desktop.autoapprove
PORTAL_DIR=/usr/share/xdg-desktop-portal/portals
DBUS_DIR="$HOME/.local/share/dbus-1/services"
UNIT_DIR="$HOME/.config/systemd/user"
CONF_DIR="$HOME/.config/xdg-desktop-portal"
CONF="$CONF_DIR/gnome-portals.conf"
MODE=rustdesk-connected

install_all() {
  sudo install -Dm644 "$REPO/install/autoapprove.portal" \
       "$PORTAL_DIR/autoapprove.portal"

  mkdir -p "$DBUS_DIR" "$UNIT_DIR" "$CONF_DIR"

  cat > "$DBUS_DIR/$NAME.service" <<EOF
[D-BUS Service]
Name=$NAME
Exec=/usr/bin/python3 $REPO/portal-autoapprove.py
SystemdService=portal-autoapprove.service
EOF

  cat > "$UNIT_DIR/portal-autoapprove.service" <<EOF
[Unit]
Description=Portal service (autoapprove ScreenCast implementation)
After=graphical-session.target
Requisite=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=dbus
BusName=$NAME
ExecStart=/usr/bin/python3 $REPO/portal-autoapprove.py --auto-approve-when $MODE
Restart=on-failure
RestartSec=1
EOF

  cat > "$CONF" <<'EOF'
[preferred]
default=gnome;gtk;
org.freedesktop.impl.portal.Secret=gnome-keyring;
org.freedesktop.impl.portal.ScreenCast=autoapprove;gnome;
EOF

  systemctl --user daemon-reload
  systemctl --user restart xdg-desktop-portal.service
  echo "設置しました。mode=$MODE"
  echo "元に戻すには: bash $0 --uninstall"
}

uninstall_all() {
  rm -f "$CONF" "$DBUS_DIR/$NAME.service" "$UNIT_DIR/portal-autoapprove.service"
  sudo rm -f "$PORTAL_DIR/autoapprove.portal"
  systemctl --user stop portal-autoapprove.service 2>/dev/null || true
  systemctl --user daemon-reload
  systemctl --user restart xdg-desktop-portal.service
  echo "撤去しました。GNOME の既定バックエンドに戻っています。"
}

status_all() {
  echo "--- portals.conf ---"; cat "$CONF" 2>/dev/null || echo "(なし)"
  echo "--- .portal ---";      ls -l "$PORTAL_DIR/autoapprove.portal" 2>/dev/null || echo "(なし)"
  echo "--- unit ---";         systemctl --user status portal-autoapprove.service --no-pager 2>&1 | head -5
}

case "${1:---status}" in
  --install)   shift; [ "${1:-}" = "--mode" ] && MODE="$2"; install_all ;;
  --uninstall) uninstall_all ;;
  --status)    status_all ;;
  *) echo "使い方: $0 --install [--mode MODE] | --uninstall | --status"; exit 1 ;;
esac
```

- [ ] **Step 6: 中継のみのバックエンドを設置して挙動が変わらないことを確認**

```bash
chmod +x portal-autoapprove.py portal-autoapprove-install.sh
bash portal-autoapprove-install.sh --install --mode never
journalctl --user -f -u portal-autoapprove -u xdg-desktop-portal &
```

その状態で RustDesk 接続を試みる。

Expected:
- journal に `registered org.freedesktop.impl.portal.desktop.autoapprove at /org/freedesktop/portal/desktop (fallback=org.freedesktop.impl.portal.desktop.gnome)` が出る
- `req=CreateSession ... decision=delegate reason=proxy-only` が出る
- **「画面を共有」ダイアログが従来どおり出る**（＝中継が効いている）
- 承認すれば画面が見える（＝Session/Request の中継も効いている）

ダイアログが出ない・接続できない場合は配管が壊れているので、
`bash portal-autoapprove-install.sh --uninstall` で戻してから原因を調べること。

- [ ] **Step 7: どの portals.conf が読まれたか確認**

`XDG_CURRENT_DESKTOP=ubuntu:GNOME` なので `ubuntu-portals.conf` が優先される可能性がある
（設計書 9-2 の未確定事項）。Step 6 で自作バックエンドが呼ばれていれば
`gnome-portals.conf` で効いていると確認できたことになる。呼ばれていなければ:

```bash
cp ~/.config/xdg-desktop-portal/gnome-portals.conf \
   ~/.config/xdg-desktop-portal/ubuntu-portals.conf
systemctl --user restart xdg-desktop-portal.service
```

で再確認し、効いた方のファイル名を `portal-autoapprove-install.sh` の `CONF` に反映する。

- [ ] **Step 8: kill switch を確認**

```bash
bash portal-autoapprove-install.sh --uninstall
```

Expected: RustDesk 接続で従来どおりダイアログが出る。`--status` で全部消えている。
確認後に `bash portal-autoapprove-install.sh --install --mode never` で戻す。

- [ ] **Step 9: コミット**

```bash
git add portal_autoapprove/proxy.py portal_autoapprove/impl.py \
        portal-autoapprove.py install/autoapprove.portal portal-autoapprove-install.sh
git commit -m "portal-autoapprove: 全要求を xdp-gnome へ中継するバックエンドの骨格を追加"
```

---

### Task 6: 自動承認パス — Mutter を直接叩いて UI 無しでストリームを返す

**Files:**
- Modify: `portal_autoapprove/impl.py`（Task 5 で作ったもの）
- Modify: `portal-autoapprove.py`（引数をバックエンドへ渡す）

**Interfaces:**
- Consumes: `policy.decide`, `policy.is_rustdesk_connected`（Task 4）、`mutter.record_monitor`, `mutter.get_current_state`, `mutter.InhibitedError`（Task 2）、`monitors.select_connector`, `monitors.stream_geometry`（Task 1）、`protocol.*`（Task 2）
- Produces:
  - `impl.ScreenCastBackend(bus, fallback_backend_name, prefer_connector, mode, retry_seconds, policy_grace_ms)`
  - `impl.Session` に属性 `route`（`"approve"` または `"delegate"`）, `cursor_mode`, `recording` が増える

**設計上の要点:** **判定は `CreateSession` で一度だけ行い、そのセッションの経路を固定する。**
`SelectSources` で判定すると、中継経路に入るべきセッションが GNOME 側に存在しないことになる
（GNOME は `CreateSession` を受け取っていないため）。

- [ ] **Step 1: impl.py の import と定数を差し替える**

`portal_autoapprove/impl.py` の先頭の import を次に置き換える:

```python
import logging
import time

from gi.repository import Gio, GLib

from portal_autoapprove import monitors, mutter, policy, protocol, proxy

ROUTE_APPROVE = "approve"
ROUTE_DELEGATE = "delegate"
```

- [ ] **Step 2: Session クラスを差し替える**

```python
class Session:
    """1つの画面共有セッション。

    route は CreateSession のときに決めて以後変えない。SelectSources や Start で
    決め直すと、中継経路に入るべきセッションが GNOME 側に存在しないことになる。
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

    def close(self):
        if self.recording is not None:
            self.recording.stop()
            self.recording = None
        if self.route == ROUTE_DELEGATE:
            self.backend.fallback.close_object(self.path, SESSION_IFACE)
```

- [ ] **Step 3: コンストラクタと CreateSession を差し替える**

```python
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
```

`_create_session` を次に置き換える:

```python
    def _create_session(self, params, invocation):
        handle, session_handle, app_id, _options = params.unpack()
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
            self._export_request(handle)
            self._export_session(session_handle, route, decision.reason)

            if route == ROUTE_DELEGATE:
                self.fallback.forward("CreateSession", params, invocation)
                return GLib.SOURCE_REMOVE
            invocation.return_value(
                GLib.Variant("(ua{sv})", (protocol.RESPONSE_SUCCESS, {})))
            return GLib.SOURCE_REMOVE

        settle()
```

`_export_session` のシグネチャを合わせる:

```python
    def _export_session(self, path, route, reason):
        session = Session(self, path, route, reason)
        session.registration_id = self.bus.register_object(
            path, self._session_info.interfaces[0],
            self._on_session_method_call, None, None)
        if route == ROUTE_DELEGATE:
            session.closed_subscription = self.fallback.subscribe_closed(
                path, lambda: self._session_closed(path))
        self._sessions[path] = session
```

`_session_closed` の購読解除を route に合わせる（`closed_subscription` が None なら何もしない
という既存の条件でそのまま動くので変更不要）。

- [ ] **Step 4: SelectSources を差し替える**

```python
    def _select_sources(self, params, invocation):
        handle, session_handle, _app_id, options = params.unpack()
        self._export_request(handle)
        session = self._sessions.get(session_handle)

        if session is None or session.route == ROUTE_DELEGATE:
            self.fallback.forward("SelectSources", params, invocation)
            return

        # 要求された cursor_mode をそのまま覚える。勝手に変えない。
        session.cursor_mode = options.get("cursor_mode", protocol.CURSOR_MODE_HIDDEN)
        self.log.info("req=SelectSources session=%s cursor_mode=%d",
                      session_handle, session.cursor_mode)
        invocation.return_value(
            GLib.Variant("(ua{sv})", (protocol.RESPONSE_SUCCESS, {})))
```

- [ ] **Step 5: Start を差し替える**

```python
    def _start(self, params, invocation):
        handle, session_handle, app_id, _parent, _options = params.unpack()
        self._export_request(handle)
        session = self._sessions.get(session_handle)

        if session is None or session.route == ROUTE_DELEGATE:
            self.fallback.forward("Start", params, invocation)
            return

        started = time.monotonic()
        state = mutter.get_current_state(self.bus)
        connector = monitors.select_connector(state, self.prefer_connector)
        position, size = monitors.stream_geometry(state, connector)

        def on_ready(recording):
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

        def on_error(exc):
            self.log.error('req=Start session=%s app_id="%s" decision=approve '
                           "connector=%s error=%s", session_handle, app_id,
                           connector, exc)
            invocation.return_value(
                GLib.Variant("(ua{sv})", (protocol.RESPONSE_OTHER, {})))

        mutter.record_monitor(self.bus, connector, session.cursor_mode,
                              on_ready, on_error)
```

`results` に入れるのは `streams` だけであること。**`restore_data` / `persist_mode` は返さない**
（spec 5.3）。ダイアログが原理的に出ないので永続化は不要で、いま壊れている permission store
に依存しないため。`SelectSources` で `restore_token` を受け取っても無視してよい。

（`on_error` の中継フォールバックは Task 8 で足す。この時点ではエラーを返すだけ。）

- [ ] **Step 6: エントリポイントから引数を渡す**

`portal-autoapprove.py` の `backend = impl.ScreenCastBackend(...)` を差し替える:

```python
    backend = impl.ScreenCastBackend(
        bus,
        "org.freedesktop.impl.portal.desktop.%s" % opts["--fallback-backend"],
        opts["--prefer-connector"],
        opts["--auto-approve-when"],
        int(opts["--retry-seconds"]),
        int(opts["--policy-grace-ms"]))
```

- [ ] **Step 7: 単体テストが壊れていないことを確認**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS — 35 tests（Task 1-2 の 21 + policy の 14）（impl.py に単体テストは無いが、純関数モジュールが壊れていないこと）

- [ ] **Step 8: 実機で自動承認を確認**

```bash
bash portal-autoapprove-install.sh --install --mode rustdesk-connected
journalctl --user -f -u portal-autoapprove &
```

RustDesk で接続する。

Expected:
- **ダイアログが出ずに画面が見える**
- journal に `decision=approve reason=rustdesk-cm-running connector=Virtual-1 node_id=<数字>` が出る
- 一度切断して再接続しても、やはりダイアログが出ない

`decision=delegate reason=rustdesk-not-connected` と出てダイアログが出る場合は、
`--cm` の起動が ScreenCast 要求より後になっている（設計書 9-1）。
`--mode always` で再試験し、それで通るなら Task 4 Step 5 に戻って指標を見直すこと。

- [ ] **Step 9: コミット**

```bash
git add portal_autoapprove/impl.py portal-autoapprove.py
git commit -m "portal-autoapprove: ポリシー合致時に Mutter を直接叩いて UI 無しで承認する"
```

---

### Task 7: E2E テストツール — RustDesk 無しで確かめる

**Files:**
- Create: `screencast-client-test.py`

**Interfaces:**
- Consumes: 稼働中の `org.freedesktop.portal.ScreenCast`（フロントエンド）
- Produces: なし（CLI ツール）

**背景（実装者向け）:** フロントエンド API はリクエスト／応答シグナル方式。
`CreateSession` はオブジェクトパスを返し、実際の結果は `org.freedesktop.portal.Request` の
`Response` シグナルで届く。**シグナルがメソッドの戻り値より先に届くことがある**ので、
パスを問わず購読しておき、届いた結果を退避しておく作りにする。

- [ ] **Step 1: E2E テストツールを実装**

`screencast-client-test.py`:

```python
#!/usr/bin/env python3
"""ScreenCast ポータルをフロントエンド側から叩く E2E テスト。

RustDesk を使わずに「ダイアログが出ずに PipeWire の node_id が返るか」を確かめる。

  python3 screencast-client-test.py            # 一連の流れを実行して node_id を表示
  python3 screencast-client-test.py --cursor 4 # cursor_mode を指定 (1=hidden 2=embedded 4=metadata)

終了コード 0 = 成功、1 = 失敗、2 = ポータルが応答しない。
"""
import os
import sys

from gi.repository import Gio, GLib

BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"

SOURCE_TYPE_MONITOR = 1


class Client:
    def __init__(self, bus):
        self.bus = bus
        self.loop = GLib.MainLoop()
        self.pending = {}   # request path -> callback
        self.early = {}     # request path -> (response, results)
        self.failure = None
        self.token = 0
        # パスを問わず Response を購読する。メソッドの戻り値より先に届くことがあるため。
        self.bus.signal_subscribe(
            BUS, REQUEST_IFACE, "Response", None, None,
            Gio.DBusSignalFlags.NONE, self._on_response, None)

    def _on_response(self, _conn, _sender, path, _iface, _signal, params):
        response, results = params.unpack()
        callback = self.pending.pop(path, None)
        if callback is None:
            self.early[path] = (response, results)
            return
        callback(response, results)

    def _next_token(self):
        self.token += 1
        return "clienttest%d" % self.token

    def call(self, method, args, options, on_result):
        """ポータルのメソッドを呼び、Response シグナルの結果を on_result に渡す。"""
        options = dict(options)
        options["handle_token"] = GLib.Variant("s", self._next_token())
        params = GLib.Variant.new_tuple(*(list(args) + [GLib.Variant("a{sv}", options)]))

        def on_done(_source, res):
            try:
                path = self.bus.call_finish(res).unpack()[0]
            except GLib.Error as err:
                self.fail("%s の呼び出しに失敗: %s" % (method, err.message))
                return
            if path in self.early:
                response, results = self.early.pop(path)
                on_result(response, results)
                return
            self.pending[path] = on_result

        self.bus.call(BUS, PATH, IFACE, method, params,
                      GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE,
                      -1, None, on_done)

    def fail(self, message):
        self.failure = message
        self.loop.quit()


def main(argv):
    cursor_mode = 2
    if "--cursor" in argv:
        cursor_mode = int(argv[argv.index("--cursor") + 1])
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    client = Client(bus)
    state = {"session": None, "node_id": None}

    def on_started(response, results):
        if response != 0:
            client.fail("Start が response=%d を返した" % response)
            return
        streams = results.get("streams") or []
        if not streams:
            client.fail("streams が空")
            return
        node_id, props = streams[0]
        state["node_id"] = node_id
        print("成功: node_id=%d position=%s size=%s source_type=%s"
              % (node_id, props.get("position"), props.get("size"),
                 props.get("source_type")))
        client.loop.quit()

    def on_sources_selected(response, _results):
        if response != 0:
            client.fail("SelectSources が response=%d を返した" % response)
            return
        client.call("Start", [GLib.Variant("o", state["session"]),
                              GLib.Variant("s", "")], {}, on_started)

    def on_session_created(response, results):
        if response != 0:
            client.fail("CreateSession が response=%d を返した" % response)
            return
        state["session"] = results["session_handle"]
        print("session=%s" % state["session"])
        client.call("SelectSources", [GLib.Variant("o", state["session"])], {
            "types": GLib.Variant("u", SOURCE_TYPE_MONITOR),
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", cursor_mode),
        }, on_sources_selected)

    client.call("CreateSession", [], {
        "session_handle_token": GLib.Variant("s", "clienttest%d" % os.getpid()),
    }, on_session_created)

    GLib.timeout_add_seconds(30, lambda: (client.fail("30秒で応答なし"), False)[1])
    client.loop.run()

    if client.failure:
        print("失敗: %s" % client.failure, file=sys.stderr)
        return 2 if "応答なし" in client.failure else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 2: 中継モードで動作を確認**

```bash
bash portal-autoapprove-install.sh --install --mode never
python3 screencast-client-test.py; echo "exit=$?"
```

Expected: **ダイアログが出る**。手で承認すれば `成功: node_id=<数字>` と `exit=0`。
キャンセルすれば `Start が response=1 を返した` と `exit=1`。中継が正しく効いている証拠。

- [ ] **Step 3: 自動承認モードで動作を確認**

```bash
bash portal-autoapprove-install.sh --install --mode always
python3 screencast-client-test.py; echo "exit=$?"
```

Expected: **ダイアログが出ずに** `成功: node_id=<数字> position=(0, 0) size=(3840, 2160) source_type=1`、`exit=0`。

- [ ] **Step 4: 既定モードに戻す**

```bash
bash portal-autoapprove-install.sh --install --mode rustdesk-connected
python3 screencast-client-test.py; echo "exit=$?"
```

Expected: RustDesk が接続していなければダイアログが出る（`--cm` が無いので中継される）。
これがポリシーが効いている証拠。

- [ ] **Step 5: コミット**

```bash
git add screencast-client-test.py
git commit -m "screencast-client-test: RustDesk 無しでポータル経路を検証する E2E ツールを追加"
```

---

### Task 8: リトライと中継フォールバック

**Files:**
- Modify: `portal_autoapprove/impl.py`

**Interfaces:**
- Consumes: `mutter.InhibitedError`（Task 2）
- Produces: `impl.Session` に属性 `create_params`, `select_params` が増える

**設計上の要点:** Mutter が失敗したときに「中継へ落とす」には、**`CreateSession` と
`SelectSources` を GNOME バックエンドに対して張り直す必要がある**。承認経路ではそれらを
GNOME に送っていないため、いきなり `Start` だけ中継しても GNOME 側にセッションが無い。
そこで承認経路でも元の引数を保存しておき、失敗時に順番に流し直す。

- [ ] **Step 1: Session に元の引数を保存する**

`Session.__init__` に追加:

```python
        self.create_params = None
        self.select_params = None
```

`_create_session` の `settle()` 内、承認分岐で保存する（`invocation.return_value(...)` の直前）:

```python
            session = self._sessions[session_handle]
            session.create_params = params
```

`_select_sources` の承認分岐で保存する（`invocation.return_value(...)` の直前）:

```python
        session.select_params = params
```

- [ ] **Step 2: リトライ付きの記録開始に差し替える**

`_start` の中の `mutter.record_monitor(...)` 呼び出しを次に置き換える:

```python
        deadline = time.monotonic() + self.retry_seconds

        def attempt():
            mutter.record_monitor(self.bus, connector, session.cursor_mode,
                                  on_ready, on_error)
            return GLib.SOURCE_REMOVE

        def on_error(exc):
            if isinstance(exc, mutter.InhibitedError) and time.monotonic() < deadline:
                # RustDesk は接続時に uinput で入力を注入するので、画面ブランク由来の
                # inhibit ならこの待ちの間に解除されることがある。
                self.log.info("req=Start session=%s inhibited、500ms 後に再試行",
                              session_handle)
                GLib.timeout_add(500, attempt)
                return
            self.log.warning('req=Start session=%s app_id="%s" decision=approve '
                             "connector=%s error=%s → 中継に切り替える",
                             session_handle, app_id, connector, exc)
            self._delegate_from_scratch(session, params, invocation)

        attempt()
```

- [ ] **Step 3: 中継への切り替えを実装**

`ScreenCastBackend` にメソッドを追加する:

```python
    def _delegate_from_scratch(self, session, start_params, invocation):
        """承認に失敗したセッションを GNOME バックエンドへ張り直して中継する。

        承認経路では CreateSession / SelectSources を GNOME に送っていないので、
        Start だけ中継しても向こうにセッションが無い。保存しておいた元の引数を
        順番に流し直してから Start を中継する。
        """
        session.route = ROUTE_DELEGATE
        session.closed_subscription = self.fallback.subscribe_closed(
            session.path, lambda: self._session_closed(session.path))

        if session.create_params is None or session.select_params is None:
            self.log.error("session=%s 元の引数が無く中継に切り替えられない", session.path)
            invocation.return_value(
                GLib.Variant("(ua{sv})", (protocol.RESPONSE_OTHER, {})))
            return

        def fail(stage, response):
            self.log.error("session=%s 中継の %s が response=%d を返した",
                           session.path, stage, response)
            invocation.return_value(GLib.Variant("(ua{sv})", (response, {})))

        def on_start_done(_source, res):
            try:
                invocation.return_value(self.bus.call_finish(res))
            except GLib.Error as err:
                invocation.return_gerror(err)

        def on_select_done(_source, res):
            try:
                response = self.bus.call_finish(res).unpack()[0]
            except GLib.Error as err:
                invocation.return_gerror(err)
                return
            if response != protocol.RESPONSE_SUCCESS:
                fail("SelectSources", response)
                return
            self.bus.call(self.fallback.name, PORTAL_PATH,
                          proxy.SCREEN_CAST_IFACE, "Start", start_params,
                          proxy.REPLY_TYPE, Gio.DBusCallFlags.NONE, -1, None,
                          on_start_done)

        def on_create_done(_source, res):
            try:
                response = self.bus.call_finish(res).unpack()[0]
            except GLib.Error as err:
                invocation.return_gerror(err)
                return
            if response != protocol.RESPONSE_SUCCESS:
                fail("CreateSession", response)
                return
            self.bus.call(self.fallback.name, PORTAL_PATH,
                          proxy.SCREEN_CAST_IFACE, "SelectSources",
                          session.select_params, proxy.REPLY_TYPE,
                          Gio.DBusCallFlags.NONE, -1, None, on_select_done)

        self.bus.call(self.fallback.name, PORTAL_PATH, proxy.SCREEN_CAST_IFACE,
                      "CreateSession", session.create_params, proxy.REPLY_TYPE,
                      Gio.DBusCallFlags.NONE, -1, None, on_create_done)
```

- [ ] **Step 4: 単体テストが壊れていないことを確認**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS — 35 tests（Task 1-2 の 21 + policy の 14）

- [ ] **Step 5: 中継フォールバックを実機で確認**

Mutter を確実に失敗させるため、**一時的に `mutter.py` の宛先名を存在しないものに書き換える**。
`monitors.select_connector` は未知の名前を渡されても先頭にフォールバックしてしまうので、
connector 名を変えても失敗は作れない。

```bash
bash portal-autoapprove-install.sh --install --mode always
cp portal_autoapprove/mutter.py /tmp/mutter.py.bak
sed -i 's/^SCREEN_CAST_NAME = .*/SCREEN_CAST_NAME = "org.gnome.Mutter.ScreenCastNoSuchService"/' \
    portal_autoapprove/mutter.py
systemctl --user restart portal-autoapprove.service
journalctl --user -f -u portal-autoapprove &
python3 screencast-client-test.py; echo "exit=$?"
```

Expected: journal に `→ 中継に切り替える` が出て、**ダイアログが出る**。
手で承認すれば `成功: node_id=...` と `exit=0`。これで `_delegate_from_scratch` が
CreateSession → SelectSources → Start を張り直せていることが確認できる。

確認したら必ず戻す:

```bash
cp /tmp/mutter.py.bak portal_autoapprove/mutter.py
systemctl --user restart portal-autoapprove.service
git diff --exit-code portal_autoapprove/mutter.py   # 差分が無いこと
```

なお `InhibitedError` のリトライ経路（`→ inhibited、500ms 後に再試行`）は、Task 3 で
inhibit 状態の作り方が判明していればその状態で `screencast-client-test.py` を実行して確認する。
判明していなければリトライ経路の実機確認は省略し、その旨を Task 9 Step 4 に記録する。

- [ ] **Step 6: コミット**

```bash
git add portal_autoapprove/impl.py
git commit -m "portal-autoapprove: inhibit のリトライと、失敗時の中継フォールバックを追加"
```

---

### Task 9: 実機確認とドキュメント更新

**Files:**
- Modify: `README.md`
- Modify: `handoff-vkms-rustdesk.md`
- Modify: `docs/superpowers/specs/2026-09-05-portal-screencast-autoapprove-design.md`（未確定事項の解消を反映）

**Interfaces:**
- Consumes: これまでの全タスク
- Produces: なし

- [ ] **Step 1: 実機確認をひととおり行う**

既定モードで設置した状態で、以下を順に確認して結果を控える:

```bash
bash portal-autoapprove-install.sh --install --mode rustdesk-connected
journalctl --user -f -u portal-autoapprove &
```

- [ ] RustDesk で接続 → ダイアログが出ずに画面が見える
- [ ] 切断して再接続 → やはりダイアログが出ない
- [ ] DP-1 の電源を切った状態で接続 → 3840x2160 で見える（`connector=Virtual-1` がログに出る）
- [ ] `systemctl --user stop portal-autoapprove.service` してから接続 → **ダイアログが出る**（D-Bus activation で再起動するので、確実に止めるには `--uninstall`）
- [ ] `bash portal-autoapprove-install.sh --uninstall` → 従来どおりダイアログが出る（kill switch）
- [ ] 再設置して Chrome など別アプリで画面共有 → **ダイアログが出る**（RustDesk 未接続時は中継されるため）

- [ ] **Step 2: README に節を追加**

`README.md` の「5. 同梱スクリプト」の前に節を追加する。内容:

- 何を解決したか（RustDesk 接続のたびに出ていた「画面を共有」ダイアログ）
- なぜポータルのバックエンドを差し替えたのか（`impl.portal.ScreenCast` に
  `OpenPipeWireRemote` が無く、バックエンドは node_id を返すだけでよい）
- 承認範囲と残存リスク（RustDesk 接続中のみ。同時に別アプリが要求すると通る）
- kill switch（`portal-autoapprove-install.sh --uninstall`）
- 設計書と調査記録へのリンク

同梱スクリプトの表に次の4行を追加する:

```markdown
| `portal-autoapprove.py` | ScreenCast ポータルの自作バックエンド(デーモン)。単体で起動せず systemd user unit 経由 |
| `portal-autoapprove-install.sh` | 上の設置・撤去・状態確認。`--install` / `--uninstall` / `--status` |
| `screencast-probe.py` | Mutter に直接セッションを張れるか確かめる探針。`--show` / `--keep` |
| `screencast-client-test.py` | ポータルをフロントエンド側から叩く E2E テスト。RustDesk 不要 |
```

- [ ] **Step 3: handoff に運用手順を追加**

`handoff-vkms-rustdesk.md` に「画面共有ダイアログの自動承認」の節を追加する。内容:

- 設置状態の確認: `bash portal-autoapprove-install.sh --status`
- ログの見方: `journalctl --user -u portal-autoapprove`
- ダイアログが出るようになったときの切り分け手順:
  1. `journalctl --user -u portal-autoapprove` で `decision=` を見る
  2. `delegate` なら `--cm` が検出できていない → `pgrep -a -f rustdesk`
  3. `approve` なのに失敗しているなら `python3 screencast-probe.py` で Mutter 側を確認
- 元に戻す手順: `bash portal-autoapprove-install.sh --uninstall`

- [ ] **Step 4: 設計書の未確定事項を更新**

`docs/superpowers/specs/2026-09-05-portal-screencast-autoapprove-design.md` の
「9. 未確定事項」の各項目に、実装で分かった結果を追記する（`--cm` の起動順序、
実効の `portals.conf` ファイル名、inhibit の要因、`app-*.scope` での app_id 付与、
xdp-gnome の SEGV の再現条件）。解消したものは「解消: <結果>」と書く。

- [ ] **Step 5: コミット**

```bash
git add README.md handoff-vkms-rustdesk.md \
        docs/superpowers/specs/2026-09-05-portal-screencast-autoapprove-design.md
git commit -m "docs: 画面共有ダイアログ自動承認の運用手順と調査結果を反映"
```
