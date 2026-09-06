# Wayland 仮想モニタによるリモートデスクトップ維持

物理モニタの電源を切ってもリモートデスクトップ接続を維持するための、
VKMS 仮想モニタ + GNOME(Mutter) + RustDesk 構成の設計・構築記録。

- **状態**: 完成（2026-09-03 実機確認済み）。画面共有ダイアログの自動承認も追加・実機確認済み（2026-09-06、5章）
- **運用手順・トラブルシュート**: [handoff-vkms-rustdesk.md](handoff-vkms-rustdesk.md)

---

## 1. 発端

> waylandが「モニタを省電力で消灯してるとモニタis noneと判断してリモートデスクトップ接続を拒否する」
> というおF**K具合。電源を切ったモニタの代わりに仮想フレームバッファの一つでも用意して
> 仮想モニタデバイスとしてwaylandに認識させてそっちにフォールバックさせるソフトウェアを設計してほしい

### 問題の正体

「モニタが none と判断される」の実体は、DRM/KMS レイヤでの **connector の切断**である。

DisplayPort の多くのモニタは電源を切ると **HPD (Hot Plug Detect) を落とす**。
これは省電力状態(DPMS off)ではなく、OS から見れば**ケーブルを抜いたのと同じ**であり、
connector の status は `connected` → `disconnected` に変わる。

```
モニタの電源OFF → DPリンク断 → connector disconnected
                → Mutter の論理モニタが 0 個に
                → 描画先のフレームバッファが存在しない
                → 画面キャプチャの対象が無い → リモートデスクトップが成立しない
```

X11 では画面は物理出力から独立した仮想スクリーンとして存在するため、この問題は起きにくい。
Wayland では**コンポジタが実際の出力に対して直接描画する**設計なので、
出力が消えるとセッションの描画先そのものが消える。これが根本的な差である。

### 設計方針

「消えないモニタ」を1つ常に用意すればよい。
ただしユーザ空間の仮想フレームバッファではなく、**カーネルの DRM レイヤに仮想モニタを作る**。
そうすれば Mutter も RustDesk も、それを普通のモニタとして扱う — 特別対応が一切不要になる。

採用したのは **VKMS (Virtual Kernel Mode Setting)**。Linux カーネル in-tree の仮想 DRM ドライバで、
追加のユーザ空間デーモンを必要としない。

そして物理モニタと**常時ミラー**にする。拡張ではなくミラーにすることで、
「手元で見えているものがそのままリモートで見える」状態を保てる。

```
        ┌─────────────────────────────────┐
        │   GNOME / Mutter (Wayland)      │
        │   論理モニタ (0,0) 3840x2160    │
        └───────────┬─────────┬───────────┘
                    │         │            ← 同一内容をミラー
          ┌─────────▼──┐   ┌──▼──────────┐
          │  DP-1      │   │  Virtual-1  │
          │  (物理 4K) │   │  (VKMS)     │
          └────────────┘   └──────┬──────┘
             電源OFFで消える       │ 常に connected
                                   ▼
                             RustDesk がキャプチャ
```

---

## 2. 完成した構成

```
Virtual-1  3840x2160 @ 59.993Hz  ┐ ミラー (0,0) primary
DP-1       3840x2160 @ 59.997Hz  ┘
```

物理モニタを消すと Virtual-1 単独の 3840x2160 に自動で切り替わり、
再点灯するとミラーに自動復帰する。RustDesk の接続はその間ずっと維持される。

### 永続化されている設定

| 場所 | 内容 | 役割 |
|---|---|---|
| `/etc/default/grub` | `video=Virtual-1:3840x2160@60` | Virtual-1 に 4K モードを注入 |
| `/etc/modprobe.d/vkms.conf` | `options vkms create_default_dev=1 enable_cursor=1` | vkms のオプション |
| `/etc/modules-load.d/vkms.conf` | `vkms` | 起動時に自動ロード |
| `~/.config/monitors.xml` | 3パターンの画面構成 | ミラー / DP-1消灯時 / DP-1のみ |
| `~/.config/xdg-desktop-portal/*portals.conf` | `org.freedesktop.impl.portal.ScreenCast=autoapprove;` | ScreenCast のバックエンドを自作に固定（5章） |
| `~/.config/systemd/user/portal-autoapprove.service` | `Type=dbus` + `BusName=`、D-Bus activation | 自作バックエンドの起動 |
| `/usr/share/xdg-desktop-portal/portals/autoapprove.portal` | バックエンド宣言（設置に root が一度だけ必要） | xdp に自作実装の存在を伝える |
| `gsettings` (`org.gnome.desktop.session idle-delay`) | `0` | アイドル画面ブランクによる remote access の inhibit を止める（5章） |

### 環境

Ubuntu Desktop 24.04 / GNOME (mutter 46.2) / Wayland / kernel 7.0.0-30-generic /
NVIDIA GPU (nvidia-drm modeset=1) / HP 27f 4k (DP-1) / RustDesk

---

## 3. 構築の経緯

### Step 1: VKMS で仮想モニタを作る ✅

```bash
sudo modprobe vkms create_default_dev=1 enable_cursor=1
sudo udevadm trigger --subsystem-match=drm
sudo udevadm settle
```

`create_default_dev=1` で既定の仮想デバイスが作られ、`/dev/dri/card0` と
connector `Virtual-1` が生成される。`enable_cursor=1` はハードウェアカーソル面を有効にする。

`udevadm trigger` が要る点に注意。これを打たないと udev がデバイスノードを整えず、
GNOME 側が新しい DRM デバイスに気づかない。

**GNOME(Mutter) は Virtual-1 を自動検出した。** 特別な設定は不要だった。

```bash
gdbus call --session --dest org.gnome.Mutter.DisplayConfig \
  --object-path /org/gnome/Mutter/DisplayConfig \
  --method org.gnome.Mutter.DisplayConfig.GetCurrentState
```

### Step 2: 設計の核心を実証 ✅

RustDesk の共有ディスプレイ選択画面に Virtual-1 が「不明なディスプレイ」として出現。
これを選択した状態で**物理モニタの電源を切り、リモート接続が維持されることを実機で確認**。

この時点で「仮想モニタにフォールバックさせる」という設計自体の妥当性が証明された。
残りはすべて品質の問題（解像度とミラーリング）である。

### Step 3: 解像度を 4K に揃える ✅

Virtual-1 の初期解像度は 1024x768 で、モードリストに 3840x2160 が存在しなかった。

当初は「DP-1 の EDID をコピーして Virtual-1 に読ませる」方向で進めていたが、
これは**袋小路だった**（→ [4. 袋小路だった EDID 注入](#4-袋小路だった-edid-注入)）。

解決策は EDID を経由せず、**カーネルコマンドラインでモードを直接注入**すること。

```
GRUB_CMDLINE_LINUX_DEFAULT="... video=Virtual-1:3840x2160@60"
```

`drm_helper_probe_add_cmdline_mode()` は EDID とは無関係に、
既存モードに無い解像度を CVT/GTF で生成して `probed_modes` に追加する。
`video=` は connector 名でマッチするため、モジュールが後からロードされても効く。

```bash
$ grep -x 3840x2160 /sys/class/drm/card0-Virtual-1/modes
3840x2160
```

### Step 4: ミラーリング ✅

**GNOME 設定アプリからはこのミラーを設定できなかった。**
UI の「ミラー」は**リフレッシュレートまで完全一致**するモードしか候補に出さないため。

| モニタ | 3840x2160 のリフレッシュレート |
|---|---|
| Virtual-1 | **59.993 のみ**（`video=` の GTF 生成で1つだけ） |
| DP-1 | 60.000 / 59.997 / 59.940 / 50 / 29.97 / 25 / 23.976 |

一致するものが1つも無い。

しかし **Mutter 本体の検証は論理モニタ内の解像度（幅×高さ）一致のみ**で、
リフレッシュレートはモニタごとに異なっていて構わない。
つまり制約は UI 側にしかない。D-Bus から直接適用すれば通る。

これを確かめる手段が `ApplyMonitorsConfig` の **method=0 (VERIFY)** で、
実際には適用せず構成の妥当性だけを検証できる。副作用ゼロで仮説を検証できた。

```
[受理] DP-1 + Virtual-1 をミラー / HDMI-1 は無効
```

確認後 method=2 (PERSISTENT) で適用。→ `mirror-apply.py`

### Step 5: 物理モニタ消灯時のフォールバック ✅

DP-1 を消すと Virtual-1 が 1024x768 に落ちる問題が残った。

`monitors.xml` は**接続中モニタの組み合わせをキーにして構成を選ぶ**。
DP-1 が消えると集合が `{DP-1, Virtual-1}` → `{Virtual-1}` に変わるため、
`{Virtual-1}` 単独用の構成が無いとフォールバックし、各モニタの **preferred モード**が使われる。

```
Virtual-1  preferred = 1024x768@60.004   ← vkms の XRES_DEF/YRES_DEF 由来
DP-1       preferred = 3840x2160@59.997
```

`video=` で足したモードは `DRM_MODE_TYPE_USERDEF` であって `PREFERRED` ではないため、
preferred は 1024x768 のまま上書きされない。

対処は `{Virtual-1}` 単独用の構成を一度保存しておくこと。
DP-1 を消した状態で GNOME 設定アプリから 4K に変更するか、
`python3 mirror-apply.py --virtual-only` を実行すればよい。
以後は DP-1 を消すたびに Mutter がこの構成を自動適用する。

（DP-1 消灯中は設定アプリに Virtual-1 しか出ないため、
ミラー時のようなリフレッシュレート一致の制約を受けず UI から普通に変更できる。）

---

## 4. 袋小路だった EDID 注入

**この kernel の vkms は EDID を一切扱えない。** 同じ道に入らないための記録。

当初は「DP-1 の EDID をコピーして Virtual-1 に読ませれば、
DP-1 と同じモードリストが手に入る」と考えていた。実際には不可能だった。

### 決め手

vkms モジュールが `drm_edid_*` を一つも参照していない。

```
$ nm -u vkms.ko | grep -iE "edid|add_modes"
                 U drm_add_modes_noedid
                 U drm_set_preferred_mode
```

`vkms_conn_get_modes()` は無条件に `drm_add_modes_noedid()` を呼んで34モードを返すだけ。
DRM コアが firmware EDID / override を適用するのは
**`get_modes()` が 0 を返した場合のフォールバック**なので、vkms では永久にその分岐に到達しない。

### 3経路すべてを実測で潰した

| 経路 | 結果 |
|---|---|
| `drm.edid_firmware` | パラメータ設定済み・ファイル md5 一致でも `edid` は **0 バイト** |
| configfs `connectors/<name>/` | 属性は `status` と `possible_encoders/` のみ。**`edid` が存在しない** |
| debugfs `edid_override` | **書き込みは成功**したのに `edid` 0 バイトのまま・3840x2160 も出ない |

`edid_override` は `edid_firmware` より優先度が高い経路である。
それすら無視された時点で「EDID を読む処理自体が呼ばれていない」ことが確定した。

また、当初のモードリスト（4096x2160 / 2560x1600 / 1856x1392 / 1792x1344 …）は
DRM 組み込みの「EDID なし」既定モード表そのもので、3840x2160 は元々この表に含まれない。

### 教訓

**「設定が反映されない」と「その処理が呼ばれていない」は区別して切り分ける。**

パラメータもファイルも正しいのに結果が変わらないとき、設定値を疑い続けても進まない。
このケースでは以下の3つがそれぞれ独立に同じ結論を指していた。

- カーネルログに EDID 関連メッセージが**皆無**（失敗ログすら無い＝呼ばれていない）
- モードリストが既定表と**完全に一致**（EDID が一度も使われていない）
- モジュールのシンボルに EDID 関数が**存在しない**（機能自体が無い）

特に3つ目の `nm -u` によるシンボル確認は、カーネルソースを読まずに
「そのドライバがその機能を持っているか」を数秒で判定できる。

---

## 5. 画面共有ダイアログの自動承認

RustDesk で接続するたびに GNOME の「画面を共有」ダイアログ（xdg-desktop-portal の
ScreenCast 承認 UI）が出る。無人運用ではこれを押せる人間がおらず、
「画面が見えないと承認できないが、承認しないと画面が見えない」という循環に陥る。
これを解決した記録。

- **設計書**: [docs/superpowers/specs/2026-09-05-portal-screencast-autoapprove-design.md](docs/superpowers/specs/2026-09-05-portal-screencast-autoapprove-design.md)
- **inhibit 調査の記録**: [docs/superpowers/findings/2026-09-06-inhibit-investigation.md](docs/superpowers/findings/2026-09-06-inhibit-investigation.md)
- **運用手順**: [handoff-vkms-rustdesk.md](handoff-vkms-rustdesk.md)

### 原因は2層あった

**層1: gnome-shell のアイドル画面ブランクが remote access を inhibit していた。**
`org.gnome.desktop.session idle-delay`（既定 900 秒）でブランクすると
スクリーンシールドが立ち、`lock-enabled=false` にしていてもシールドは立つ。
シールドが立っている間 Mutter は `Session creation inhibited` を返し、
そもそも承認をどう自動化してもセッションが作れない。
対処は `gsettings set org.gnome.desktop.session idle-delay 0`。

**層2: 層1を潰したら露出した。** RustDesk の接続ごとにダイアログを押しても、
GNOME の restore token（次回から無承認で再接続できる仕組み）が機能しない。
記録先を調べると `HPN:HP 27f 4k:3CM02743XR`（DP-1 の EDID）に紐づいていた。
ミラー構成では論理モニタが1つしかないため、GNOME はその識別子として
物理モニタ(DP-1)の EDID を記録する。DP-1 は電源 OFF で connector ごと消えるため、
この識別子に対する復元は構造的に必ず失敗する。加えて xdg-desktop-portal-gnome は
ダイアログを出したセッションを閉じる経路で毎回 SEGV する（apport の
スタックトレースより use-after-free と判明。詳細は設計書の2.4章・9章を参照）。
**どちらも我々の側で直せるものではない。**

「消えないモニタ(Virtual-1)を用意する」という本リポジトリの元の設計（1章）は
物理モニタの消灯には効いたが、ポータルの永続化層(restore token)には効かなかった。

### なぜポータルのバックエンドを差し替えたのか

層2が構造的に直せない以上、GNOME 標準のポータル実装
(`xdg-desktop-portal-gnome`)を迂回するしかない。これが成立する根拠は
実機の introspection で確認した:

```
interface org.freedesktop.impl.portal.ScreenCast {
  CreateSession(...); SelectSources(...); Start(...);
  # OpenPipeWireRemote は存在しない
};
```

`org.freedesktop.impl.portal.ScreenCast` は `CreateSession` / `SelectSources` /
`Start` の3メソッドしか持たず、`OpenPipeWireRemote` を含まない。
PipeWire への接続は `xdg-desktop-portal`（フロントエンド）が自前で行う。
つまり**バックエンドは PipeWire の node_id を返すだけでよい**。

自作バックエンド(`portal-autoapprove.py`)はこれを使い、共有対象の connector を
**名前で**（既定 `Virtual-1`）指定して Mutter に直接セッションを張る。
EDID にも論理モニタの構成にも左右されず、restore token も使わず、
SEGV する中継コードパスも通らない。

```
        ┌──────────────────────────────────────────────┐
        │  RustDesk (system unit, --cm 起動で接続を検知)  │
        └───────────────────┬──────────────────────────┘
                             │ ScreenCast 要求
                             ▼
                  ┌─────────────────────┐
                  │ xdg-desktop-portal   │  ← フロントエンド。PipeWire 接続はここが行う
                  └──────────┬──────────┘
                             │ impl.portal.ScreenCast
              ┌──────────────┴───────────────┐
   ポリシー合致 │                              │ 不合致 / 失敗
              ▼                              ▼
   ┌─────────────────────┐      ┌────────────────────────────┐
   │ portal-autoapprove   │      │ xdg-desktop-portal-gnome    │
   │ (自作・ダイアログ無し) │      │ (GNOME 標準・ダイアログあり)  │
   └──────────┬───────────┘      └────────────────────────────┘
              │ connector="Virtual-1" で直接
              ▼
   org.gnome.Mutter.ScreenCast → node_id
```

### 承認範囲と残存リスク

ポータルの impl backend からは要求元プロセスを同定できない。呼び出し元は
xdg-desktop-portal 自身であり、`app_id` は非サンドボックスのアプリでは空文字列になる
（実機でも `app_id=""` を確認）。そのため承認ポリシーは「要求元の同定」ではなく
「`rustdesk --cm`（Connection Manager。接続中のみ存在するプロセス）が動いているか」
という**文脈の近似**にとどめている。

**残存リスク: RustDesk 接続中に別アプリが画面キャプチャを要求すると、それも通る。**
要求元を厳密に同定する手段が無い以上、原理的に閉じられない。
全要求は判定理由つきで journal に記録しているが、そこから再構成できるのは
「いつ・どの connector がキャプチャされたか」までで、「どのアプリが」までは分からない。
`app_id` は常に空文字列になるため、承認ログの行は文言としてはどれも同じになる
（セッションハンドルの接頭辞は呼び出し元のバス名を表すので同時に動く複数クライアントは
区別できるが、そのクライアントが終了すれば意味を失う）。

**副作用: ウィンドウ単位の画面共有がマシン全体で使えなくなる。**
このバックエンドを設置している間、ウィンドウ単位の画面共有はマシン全体で使えなくなる
（Zoom や Meet で「特定のウィンドウだけ共有」を選べない）。モニタ全体の共有は使える。
撤去すれば戻る。理由は、承認パスが常に `RecordMonitor` を呼ぶため、ウィンドウを名乗ると
要求より多く共有してしまうから（詳細は設計書 5.3）。中継パスも含め、このバックエンドを
設置している間はマシン全体でこの制約がかかる（「従来どおり」なのは中継されるダイアログの
挙動であって、`AvailableSourceTypes` の公開値は中継パスにも及ぶ）。

### 実機での結果（2026-09-06）

最終検証: RustDesk 実接続（本番設定、`--mode rustdesk-connected`）。
ユーザーがスマホから RustDesk で接続し、デスクトップ画面を確認できた。
物理モニタ(DP-1)は消灯したまま。

```
19:38:53 req=CreateSession session=/…/1_668/u1 app_id="" decision=approve reason=rustdesk-cm-running
19:38:53 req=SelectSources session=/…/1_668/u1 cursor_mode=1
19:38:53 req=Start session=/…/1_668/u1 app_id="" decision=approve reason=rustdesk-cm-running
                  connector=Virtual-1 cursor_mode=1 node_id=88 elapsed=5ms
19:39:11 session closed: /…/1_668/u1

ダイアログが出た回数: 0
xdg-desktop-portal-gnome の SEGV: 0
DP-1: disconnected
```

E2E ツール（デーモンを `--auto-approve-when always` で起動した状態で
`screencast-client-test.py` を実行、DP-1 消灯中）でも確認済み:

```
成功: node_id=88 position=(0, 0) size=(3840, 2160) source_type=1   exit=0
req=Start … decision=approve reason=mode-always connector=Virtual-1 cursor_mode=2
            node_id=88 elapsed=9ms
ダイアログの回数: 0
```

### kill switch

```bash
bash portal-autoapprove-install.sh --uninstall
```

sudo が使えない場合は `rm -f ~/.config/xdg-desktop-portal/*portals.conf &&
systemctl --user restart xdg-desktop-portal.service` でも GNOME の既定動作に戻る。

---

## 6. 同梱スクリプト

| スクリプト | 用途 |
|---|---|
| `mirror-apply.py` | ミラー構成を適用。`--virtual-only` / `--verify` / `--show` |
| `vkms-verify.sh` | 起動後の検証（cmdline / vkms ロード / 4K モードの有無）root 不要 |
| `vkms-apply.sh` | grub + 永続化の初期設定（適用済み・再実行不要） |
| `vkms-probe.sh` | EDID 経路の切り分け調査用（参考。EDID は不可能と確定済み）要 root |
| `portal-autoapprove.py` | ScreenCast ポータルの自作バックエンド(デーモン)。単体で起動せず systemd user unit 経由 |
| `portal-autoapprove-install.sh` | 上の設置・撤去・状態確認。`--install` / `--uninstall` / `--status` |
| `screencast-probe.py` | Mutter に直接セッションを張れるか確かめる探針。`--show` / `--keep` |
| `screencast-client-test.py` | ポータルをフロントエンド側から叩く E2E テスト。RustDesk 不要 |

```bash
bash vkms-verify.sh              # カーネル側
python3 mirror-apply.py --show   # GNOME 側の現在構成
```

---

## 7. 制約と、他環境へ持っていく場合の注意

- **vkms の preferred モードは 1024x768 固定**（コンパイル時定数 `XRES_DEF`/`YRES_DEF`）。
  そのため DP-1 消灯時の 4K は `monitors.xml` の構成に依存しており、
  `monitors.xml` を消すと 1024x768 に戻る。復旧は `mirror-apply.py --virtual-only`。
- **kernel によっては vkms が EDID に対応している可能性がある。**
  `nm -u vkms.ko | grep edid` で `drm_edid_*` を参照していれば、
  configfs 経由の EDID 設定が使えて話が簡単になるかもしれない。
- **モニタの組み合わせが変わるたびに構成の保存が要る。**
  `monitors.xml` は組み合わせをキーに引くため、モニタを増減したら
  その組み合わせで一度 `mirror-apply.py` を実行しておく。
- **DP 以外の接続では前提が変わる。** HDMI は電源を切っても HPD を維持する製品があり、
  その場合そもそもこの問題が起きない（＝ HDMI ダミープラグという回避策が成立する理由）。
  本構成はダミープラグと違い、実在しない解像度・配置を自由に決められる点が利点。
- **GNOME 以外のコンポジタでは Step 4-5 が変わる。** Step 1-3（VKMS + `video=`）は
  コンポジタ非依存だが、ミラー設定と構成の永続化はコンポジタ固有の仕組みに依存する。
