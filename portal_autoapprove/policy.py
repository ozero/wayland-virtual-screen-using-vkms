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
        if b"--cm" not in argv:
            continue
        if any(b"rustdesk" in arg for arg in argv):
            return True
    return False
