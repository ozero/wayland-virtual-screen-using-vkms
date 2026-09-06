# VKMS仮想モニタ + GNOME + RustDesk 構成

## 環境
- ホスト: `rg-ryzen-ubuntu-bm`
- Ubuntu Desktop 24.04、GNOME (mutter 46.2)、Wayland
- カーネル: 7.0.0-30-generic
- GPU: NVIDIA (nvidia-drm, modeset=Y)
- VKMS 仮想モニタ: `Virtual-1`
- 物理モニタ: `DP-1`（HP 27f 4k、3840x2160）
- HDMIダミープラグ: `HDMI-1`（BBC HDP-V104）→ **物理的に取り外し済み（2026-09-03）**

## ゴールと達成状況（すべて達成・実機確認済み）
1. 物理モニタ(DP-1)がDPMS offになってもRustDeskのリモートデスクトップ接続を維持する — **達成済み**
2. Virtual-1をDP-1と常時ミラー表示にする（結合/拡張ではない） — **達成済み**
3. Virtual-1の解像度をDP-1と同じ3840x2160にする — **達成済み**

## 現在の構成（2026-09-03 完了）

```
Virtual-1  3840x2160 @ 59.993Hz
DP-1       3840x2160 @ 59.997Hz

論理モニタ: (0,0) primary -> ['DP-1', 'Virtual-1']   ← 1枚に統合 = ミラー
```

永続化されている設定:

| 場所 | 内容 | 目的 |
|---|---|---|
| `/etc/default/grub` | `video=Virtual-1:3840x2160@60` | Virtual-1 に 4K モードを注入 |
| `/etc/modprobe.d/vkms.conf` | `options vkms create_default_dev=1 enable_cursor=1` | vkms のオプション |
| `/etc/modules-load.d/vkms.conf` | `vkms` | 起動時に自動ロード |
| `~/.config/monitors.xml` | DP-1 + Virtual-1 のミラー構成 | GNOME の画面配置 |
| `gsettings` (`org.gnome.desktop.session idle-delay`) | `0` | アイドル画面ブランクによる remote access の inhibit を止める（画面共有ダイアログの自動承認の前提。後述） |

## 重要: EDID 注入は不可能（調査で確定）

**この kernel の vkms は EDID を一切扱えない。** 当初 `drm.edid_firmware` で DP-1 の EDID を
Virtual-1 にコピーしようとしていたが、これは原理的に不可能。**同じ轍を踏まないこと。**

### 根拠

1. **vkms モジュールが `drm_edid_*` を一つも参照していない**
   ```
   $ nm -u vkms.ko | grep -iE "edid|add_modes"
                    U drm_add_modes_noedid
                    U drm_set_preferred_mode
   ```
   `vkms_conn_get_modes()` は無条件に `drm_add_modes_noedid()` を呼んで34モードを返すだけ。
   DRM コアが firmware EDID / override を適用するのは `get_modes()` が **0 を返した場合のフォールバック**
   なので、vkms では永久にその分岐に到達しない。

2. **3経路すべてを実測で潰した**

   | 経路 | 結果 |
   |---|---|
   | `drm.edid_firmware` | パラメータ設定済み・ファイル md5 一致でも `edid` は **0 バイト** |
   | configfs `connectors/<name>/` | 属性は `status` と `possible_encoders/` のみ。**`edid` が存在しない** |
   | debugfs `edid_override` | **書き込みは成功**したのに `edid` 0 バイトのまま・3840x2160 も出ない |

   `edid_override` は `edid_firmware` より優先度が高い経路。それすら無視された時点で確定。

3. 元のモードリスト（4096x2160 / 2560x1600 / 1856x1392 …）は DRM 組み込みの
   「EDID なし」既定モード表そのもの。3840x2160 は元々この表に含まれない。

### 採用した回避策: `video=` によるモード直接注入

EDID を経由せず、カーネルコマンドラインでモードを追加する。
`drm_helper_probe_add_cmdline_mode()` は EDID と無関係に、既存モードに無い解像度を
CVT/GTF で生成して `probed_modes` に追加するため、vkms でも効く。

```
GRUB_CMDLINE_LINUX_DEFAULT="... video=Virtual-1:3840x2160@60"
```

## 重要: ミラー設定は GNOME設定アプリからは行えない

GNOME設定アプリの「ミラー」は **リフレッシュレートまで完全一致**するモードしか候補に出さない。

| モニタ | 3840x2160 のリフレッシュレート |
|---|---|
| Virtual-1 | **59.993 のみ**（`video=` の GTF 生成で1つだけ） |
| DP-1 | 60.000 / 59.997 / 59.940 / 50 / 29.97 / 25 / 23.976 |

一致するものが無いため、UI には 4K ミラーの選択肢が出ない。

一方 **Mutter 本体の検証は論理モニタ内の解像度（幅×高さ）一致のみ**で、リフレッシュレートは
モニタごとに異なっていて構わない。`ApplyMonitorsConfig` を D-Bus から直接呼べば適用できる
（`ApplyMonitorsConfig` の method=0 は VERIFY = 副作用なしの検証、で事前確認済み）。

これを行うのが `mirror-apply.py`。

## 最重要: DP-1 消灯時に Virtual-1 が 1024x768 に落ちる問題

**症状**: DP-1 の電源を切って RustDesk で Virtual-1 に接続すると 1024x768 になる。

**原因**: DP-1 の電源を切ると **DP リンクが落ちて connector が `disconnected` になる**。
Mutter から見えるモニタの集合が `{DP-1, Virtual-1}` → `{Virtual-1}` に変わり、
`{Virtual-1}` 単独に対応する構成が `monitors.xml` に無いとフォールバックして
各モニタの **preferred モード**が使われる。Virtual-1 の preferred は vkms 組み込みの
**1024x768**（`video=` で追加したモードは `DRM_MODE_TYPE_USERDEF` であって
`DRM_MODE_TYPE_PREFERRED` ではないため、preferred は上書きされない）。

```
Virtual-1  preferred = 1024x768@60.004    ← vkms の XRES_DEF/YRES_DEF 由来
DP-1       preferred = 3840x2160@59.997
```

**対処**: `{Virtual-1}` 単独用の構成を一度保存しておく。DP-1 を消した状態で
RustDesk 経由で以下を一度実行すればよい（GNOME設定アプリで手動設定しても同じ）。

```bash
python3 mirror-apply.py --virtual-only
```

以後は DP-1 を消すたびに Mutter がこの構成を自動適用し、3840x2160 が維持される。

**この構成は 2026-09-03 02:44 に設定済み**（DP-1 消灯中に GNOME設定アプリで Virtual-1 を
4K に変更する操作で作成された。DP-1 消灯中は設定アプリに Virtual-1 しか出ないため、
ミラー時のようなリフレッシュレート一致の制約を受けず UI から普通に変更できる）。
`monitors.xml` の構成一覧で `key=['Virtual-1']` のエントリがあるか確認できる:

```bash
python3 - <<'EOF'
import xml.etree.ElementTree as ET, os
t=ET.parse(os.path.expanduser("~/.config/monitors.xml"))
for i,c in enumerate(t.getroot().findall("configuration"),1):
    print(f"[{i}] key={sorted({m.find('connector').text for m in c.iter('monitorspec')})}")
EOF
```

**注意**: preferred モード自体は変えられない（vkms のコンパイル時定数）。
つまりこの問題は `monitors.xml` の構成に依存しており、`monitors.xml` を消すと再発する。

## 注意: monitors.xml はモニタの組み合わせごとに構成を持つ

`monitors.xml` は接続されているモニタの**組み合わせ単位**で `<configuration>` を保存する。
組み合わせが変わると、その組に対する保存済み構成が無い限り Mutter は既定レイアウト
（拡張・各モニタは preferred モード）にフォールバックする。

実際にダミープラグ HDMI-1 を抜いた際、{DP-1, Virtual-1, HDMI-1} 用に保存していた構成が
{DP-1, Virtual-1} にマッチせず、ミラーが解除され Virtual-1 が 1024x768 に戻った。

**モニタを抜き挿しした後にミラーが崩れたら、`python3 mirror-apply.py` を再実行するだけでよい。**
その組み合わせに対する構成が保存され、以後は自動復元される。

## 画面共有ダイアログの自動承認

RustDesk 接続のたびに出る GNOME の「画面を共有」ダイアログを無人で通す仕組み。
設計・調査の経緯は [README.md](README.md) の5章と
[docs/superpowers/specs/2026-09-05-portal-screencast-autoapprove-design.md](docs/superpowers/specs/2026-09-05-portal-screencast-autoapprove-design.md)
を参照。ここには運用者向けの手順だけを書く。

### 設置状態の確認

```bash
bash portal-autoapprove-install.sh --status
```

`portals.conf` の候補ファイルのうちどれが存在するか、`ScreenCast` の振り先、
`.portal` ファイル、systemd unit の状態がまとめて出る。

### ログの見方

```bash
journalctl --user -u portal-autoapprove
```

1行の例:

```
req=Start session=/…/1_668/u1 app_id="" decision=approve reason=rustdesk-cm-running
             connector=Virtual-1 node_id=88 elapsed=5ms
```

- `req=` — どの D-Bus メソッド呼び出しか（`CreateSession` / `SelectSources` / `Start`）
- `decision=` — `approve`（自動承認）か `delegate`（xdg-desktop-portal-gnome へ中継 =
  従来どおりダイアログが出る）
- `reason=` — 判定の根拠。`rustdesk-cm-running` = `rustdesk --cm` を検出、
  `mode-always` = `--auto-approve-when always` 設定、`rustdesk-not-connected` = `--cm` 未検出
- `connector=` / `node_id=` / `elapsed=` — 承認した場合の実際の共有対象と所要時間

### ダイアログが出るようになったときの切り分け手順

1. `journalctl --user -u portal-autoapprove -n 20` で `decision=` を見る
2. `decision=delegate reason=rustdesk-not-connected` なら `--cm` が検出できていない。
   `pgrep -a -f rustdesk` で実際の cmdline を確認する（判定は argv[0] の basename が
   `rustdesk` で、引数に `--cm` を含むことが条件。プロセス名や引数の一部が
   一致するだけでは判定に入らない）
3. ログに何も出ていないなら xdp がそもそも自作バックエンドを選んでいない。
   `bash portal-autoapprove-install.sh --status` で `ScreenCast=autoapprove;` に
   なっているか確認し、`systemctl --user restart xdg-desktop-portal.service`
4. `decision=approve` なのに画面が出ない・失敗しているなら Mutter 側の問題。
   `python3 screencast-probe.py` を実行する（`exit=2` なら inhibit されている）
5. inhibit なら `gsettings get org.gnome.desktop.session idle-delay` を確認する
   （`0` でなければそれが原因。`gsettings set org.gnome.desktop.session idle-delay 0`
   で直す）

### 元に戻す手順

```bash
bash portal-autoapprove-install.sh --uninstall
```

sudo が使えない場合は次のコマンドでも GNOME の既定動作（ダイアログが毎回出る状態）に
戻る（`.portal` ファイルの撤去には root が要るが、`portals.conf` を消せば xdp は
自作バックエンドを選ばなくなる）:

```bash
rm -f ~/.config/xdg-desktop-portal/*portals.conf && \
  systemctl --user restart xdg-desktop-portal.service
```

## 同梱スクリプト

| スクリプト | 用途 |
|---|---|
| `mirror-apply.py` | ミラー構成を適用（`--verify` 検証のみ / `--keep-hdmi` HDMI-1 を残す / `--show` 現状表示） |
| `vkms-verify.sh` | 起動後の検証（cmdline / vkms ロード / 3840x2160 の有無）root 不要 |
| `vkms-apply.sh` | grub + 永続化の初期設定（**適用済み。再実行不要**） |
| `vkms-probe.sh` | EDID 経路の切り分け調査用（**参考。EDID は不可能と確定済み**）要 root |

## 動作確認（すべて実機で確認済み・2026-09-03）
- [x] 再起動後に構成が自動復元される（DP-1 + Virtual-1 のミラー）
- [x] DP-1 消灯 → RustDesk で Virtual-1 が **3840x2160** で表示される
- [x] DP-1 を再点灯 → ミラーに自動復帰する
- [x] DP-1 消灯中もリモートデスクトップ接続が維持される

**本構成は完成。以降は保守フェーズ。**

## トラブル時の復元
```bash
ls -1t ~/.config/monitors.xml.bak.*     # 画面構成を戻す
ls -1t /etc/default/grub.bak.*          # カーネルコマンドラインを戻す（要 update-grub + 再起動）
```

## 動作確認用コマンド
```bash
cd ~/projects/wayland-virtual-compositor-solution
bash vkms-verify.sh              # カーネル側
python3 mirror-apply.py --show   # GNOME 側の現在構成
```
