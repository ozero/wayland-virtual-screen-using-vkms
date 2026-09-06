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


def _resolves_to_rustdesk(proc_root, entry):
    """/proc/<pid>/exe で実行ファイルの実体を確かめる。

    argv[0] は execve で詐称できるので、読めるときは exe を信じる。
    他ユーザーのプロセスなどで読めない場合は None を返し、呼び出し側は
    argv[0] による判定に落とす。
    """
    try:
        exe = os.readlink(os.path.join(proc_root, entry, "exe"))
    except OSError:
        return None
    return os.path.basename(exe) == "rustdesk"


def is_rustdesk_connected(proc_root="/proc"):
    """rustdesk の Connection Manager (--cm) が動いていれば True。

    RustDesk は接続を受けたときだけ `rustdesk --cm` を起動する。
    proc_root はテストで差し替えるためのもの。

    この関数の True は「画面キャプチャを無言で承認してよい」を意味するため、
    誤検知は安全上の欠陥になる。/proc/<pid>/exe はカーネルが解決する実行ファイルの
    実体で execve では詐称できないため、まずそちらを確かめる。argv[0] は
    execve(argv0=...) で任意の文字列に詐称できるので、exe が読めるときは信用せず、
    exe が読めない(他ユーザーのプロセス等)場合にだけ argv[0] の basename に
    フォールバックする。引数のどこかに rustdesk という文字列が現れるだけの
    プロセス(rustdesk という名前のディレクトリを扱う無関係なコマンド等)は弾く。
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
        if b"--cm" not in argv:
            continue
        resolved = _resolves_to_rustdesk(proc_root, entry)
        if resolved is False:
            continue          # 実体が rustdesk ではない = 詐称
        if resolved is None and os.path.basename(argv[0]) != b"rustdesk":
            continue          # exe が読めないので argv[0] で判定する
        if resolved is True or os.path.basename(argv[0]) == b"rustdesk":
            return True
    return False
