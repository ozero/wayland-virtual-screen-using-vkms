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
