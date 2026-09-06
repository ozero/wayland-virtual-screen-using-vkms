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
