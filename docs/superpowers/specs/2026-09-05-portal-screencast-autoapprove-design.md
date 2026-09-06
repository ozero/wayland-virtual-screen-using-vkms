# 画面共有ダイアログの自動承認 — 設計

RustDesk 接続のたびに出る「画面を共有」ダイアログ（xdg-desktop-portal の ScreenCast
承認 UI）を無人で通し、常に Virtual-1 を共有対象にするための設計。

- **状態**: 設計完了・未実装（2026-09-05）
- **前提となる構成**: [../../../README.md](../../../README.md)（VKMS 仮想モニタ + GNOME + RustDesk）

---

## 1. 問題

物理モニタ消灯時もリモート接続を維持する構成は完成しているが、**RustDesk で接続する
たびに GNOME の「画面を共有」ダイアログが出る**。無人運用ではこれを押せる人間がおらず、
「画面が見えないと承認できないが、承認しないと画面が見えない」という循環に陥る。

要件は2つ:

1. ダイアログを人間の操作なしで通す
2. 共有対象として **Virtual-1 を選ぶ**（常に connected な仮想モニタ。物理モニタ DP-1 は
   電源 OFF で connector が消えるため、そちらを掴むとキャプチャが死ぬ）

「1枚目の画面を選ぶ」ではなく「Virtual-1 を名前で固定する（無ければ先頭）」を採用した。
ミラー中はどちらを選んでも内容は同じだが、ミラーが崩れている状態で DP-1 を掴むと
消灯時に落ちるため、名前固定の方が目的に合う。

---

## 2. 調査で確定した事実

すべて実機（`rg-ryzen-ubuntu-bm`）での実測。設計判断の根拠なので出典を残す。

### 2.1 環境

| 項目 | 値 |
|---|---|
| xdg-desktop-portal | 1.18.4-1ubuntu2.24.04.2 |
| xdg-desktop-portal-gnome | 46.2-0ubuntu1 |
| mutter | 46.2-1ubuntu0.24.04.16 |
| RustDesk | 1.4.7（deb、`/system.slice/rustdesk.service` で root 稼働） |
| `XDG_CURRENT_DESKTOP` | `ubuntu:GNOME` |

### 2.2 ポータルの世代

```
org.freedesktop.portal.ScreenCast    version=5  AvailableSourceTypes=7  AvailableCursorModes=7
org.freedesktop.portal.RemoteDesktop version=2
```

ScreenCast は `persist_mode` / `restore_token` に対応する世代。RemoteDesktop v2 は
永続化を持たない世代。RustDesk は `wayland-restore-token` を設定に保存しており
（`~/.config/rustdesk/RustDesk_local.toml`）、入力注入は uinput（バイナリ内に 85 箇所の
参照）を使う。よって**本設計の対象は ScreenCast のみとし、RemoteDesktop portal は
差し替えない**。

### 2.3 impl 側インターフェース（実機 introspection）

```
interface org.freedesktop.impl.portal.ScreenCast {   # on org.freedesktop.impl.portal.desktop.gnome
  CreateSession(in o handle, in o session_handle, in s app_id, in a{sv} options,
                out u response, out a{sv} results);
  SelectSources(in o handle, in o session_handle, in s app_id, in a{sv} options,
                out u response, out a{sv} results);
  Start(in o handle, in o session_handle, in s app_id, in s parent_window, in a{sv} options,
        out u response, out a{sv} results);
  readonly u AvailableSourceTypes = 7;
  readonly u AvailableCursorModes = 7;
  readonly u version = 5;
};
```

**`OpenPipeWireRemote` は impl 側に存在しない。** つまりフロントエンド
（`xdg-desktop-portal`）が自前で PipeWire に接続し、返された node_id に権限を張った fd を
アプリに渡している。**バックエンドは node_id を返すだけでよい** — これが自作バックエンド
（後述 Phase 1）が成立する根拠である。

セッション／リクエストのオブジェクトは動的に export されるため上の introspection には
現れないが、`org.freedesktop.impl.portal.Session` と `org.freedesktop.impl.portal.Request`
の2インターフェースをバックエンドが export する必要がある
（`strings /usr/libexec/xdg-desktop-portal` で両名を確認）。

Mutter 側は `org.gnome.Mutter.ScreenCast` の `Version = 4`。

### 2.4 直接原因: Mutter がセッション作成を拒否している

2026-09-04 20:11〜20:15 のログ（接続を試みた時刻）:

```
20:11:02 xdg-desktop-portal-gnome: Error restoring stream from session:
           GDBus.Error:org.freedesktop.DBus.Error.Failed: Session creation inhibited
20:11:02 xdg-desktop-portal-gnome: Failed to associate portal window with parent window
20:11:11 xdg-desktop-portal:       Error deleting permission: No entry for 2b8cdc98-...
20:11:18 xdg-desktop-portal-gnome: Main process exited, code=dumped, status=11/SEGV
（20:14:56 / 20:15:04 にも同じ SEGV。計3回）
```

読み方: **restore data は見つかっており、Mutter が拒否している。**
`Session creation inhibited` は `libmutter-14.so.0` の文字列で、同じバイナリに
`meta_remote_access_controller_inhibit_remote_access` と `priv->inhibit_count > 0` がある。
つまり gnome-shell 側が remote access を inhibit している状態。

**この inhibit が立っている間は、承認を自動化しても Mutter がセッションを作らない。**
どの方式を採るにせよ、ここが最初の関門である。

inhibit の要因は未特定。ロック画面は除外済み（`org.gnome.desktop.screensaver lock-enabled = false`）。
残る有力候補はアイドル時の画面ブランク（`org.gnome.desktop.session idle-delay = 900`）。

### 2.5 副次原因: app_id が空で restore token が機能していない

restore token の保管先は permission store の `screencast` テーブル。

```
$ flatpak permissions screencast        # 実質空（1行あるが app id が空文字列）

$ flatpak permissions background        # 比較: 正常なテーブルは app id 付きで並ぶ
background  background  org.kde.krita  yes  0x00
...

$ cat /proc/2892/cgroup
0::/system.slice/rustdesk.service       # ← system unit
```

xdg-desktop-portal の app_id 導出は `sd_pid_get_user_unit()` の結果に対する

```
^app-(?:[[:alnum:]]+\-)?(.+?)(?:\-[[:alnum:]]*)(?:\.scope|\.slice)$
```

のマッチである（`strings /usr/libexec/xdg-desktop-portal` より。ログメッセージ
`Assigning app ID "%s" to pid %ld which has unit "%s"` も同バイナリにある）。

RustDesk は **system unit** にいるため `sd_pid_get_user_unit()` が失敗し、`app-` 前提の
正規表現にも構造的にマッチしない。よって `app_id` は常に空文字列で、restore token が
アプリ単位で正しく保管・再利用されない。

### 2.6 重要な帰結: 要求元プロセスは同定できない

impl backend を呼ぶのは `xdg-desktop-portal` 自身なので、D-Bus の
`GetConnectionUnixProcessID` は xdp の PID を返す。`app_id` は 2.5 の通り空。
**バックエンド側から「これは RustDesk の要求だ」と厳密に判定する手段はない。**

したがって承認ポリシーは**要求元の同定ではなく文脈の同定**で近似する（5.5）。

---

## 3. 設計方針

問題を3層に分け、層ごとに独立して検証・修正できる形にする。

```
┌─ 層3: 承認 UI ─────────────────────────────────────────────┐
│  xdg-desktop-portal-gnome が「画面を共有」ダイアログを出す      │
│  → Phase 1: 自作バックエンドで置き換え、UI を発生させない        │
└────────────────────┬───────────────────────────────────────┘
┌─ 層2: 永続化 ──────┴───────────────────────────────────────┐
│  restore_token を permission store に app_id 単位で保存        │
│  → 現状: app_id="" のため機能不全（2.5）                       │
│  → Phase 0: unit 名を app-* にして app_id を付与できるか検証     │
└────────────────────┬───────────────────────────────────────┘
┌─ 層1: Mutter ──────┴───────────────────────────────────────┐
│  org.gnome.Mutter.ScreenCast がセッションを作る                │
│  → 現状: "Session creation inhibited" で拒否（2.4）            │
│  → Phase 0: ここが通らないと層3の対策も無意味                   │
└────────────────────────────────────────────────────────────┘
```

**層1が塞がっている限り層3で何をしても無意味**なので、Phase 0 で層1から検証する。
既存プロジェクトで `ApplyMonitorsConfig` の `method=0 (VERIFY)` を使い副作用ゼロで
仮説を検証したのと同じ考え方で、**最小副作用の探針を先に打つ**。

Phase 0 の結果次第で、設定変更のみで完了しコードを書かない可能性がある。それが最良の
結末なので最初に確かめる。

---

## 4. Phase 0 — 原因究明

### 4.1 P0-2: Mutter 直接探針（最初に実施）

新規スクリプト `screencast-probe.py`。既存 `mirror-apply.py` と同じ Python +
`gi.repository.Gio` で依存追加なし。

```
org.gnome.Mutter.ScreenCast.CreateSession({})           -> /org/gnome/Mutter/ScreenCast/Session/uN
  Session.RecordMonitor("Virtual-1", {cursor-mode: 2})  -> .../Stream/uM
  Stream の PipeWireStreamAdded を購読
  Session.Start()                                       -> node_id
  結果を表示して Session.Stop()
```

| 結果 | 判定 |
|---|---|
| node_id が返る | **層1は健全。Phase 1 が成立する。**inhibit は間欠的（トリガ条件がある） |
| `Session creation inhibited` | 層1が塞がっている。**Phase 1 も動かない**。inhibit 要因の特定が先 |
| `Virtual-1` が見つからない | ミラー構成が崩れている。`mirror-apply.py` の再実行が先 |

副作用は「画面共有中」インジケータが数秒出るだけで、`Stop()` で消える。
`--keep` で張り続けて挙動を観察できるようにする。

### 4.2 P0-1: inhibit 要因の特定

`lock-enabled = false` なのでロック画面は除外済み。残る候補は2つあり、**両者は別のトリガ**
なので分けて試す。

- **候補A: gnome-shell のアイドル画面ブランク**（`idle-delay = 900`）。gnome-shell 側の状態変化
- **候補B: 物理モニタの電源 OFF による connector 切断**。DRM 側の状態変化。Mutter から
  見えるモニタ集合が `{DP-1, Virtual-1}` → `{Virtual-1}` に変わる

各状態で `screencast-probe.py`（4.1）を実行する:

| 状態 | 作り方 | 分かること |
|---|---|---|
| S0 通常 | 何もしない | 通らなければ inhibit は**恒久的** |
| S1 DP-1 電源 OFF | モニタの電源ボタンを押す（即時） | 候補B の検証 |
| S2 アイドルブランク後 | `idle-delay` を一時的に `60` にして1分放置 | 候補A の検証。**15分待つ必要はない** |
| S3 S1 + S2 | 電源 OFF のまま1分放置 | 実運用に最も近い状態 |

`idle-delay` は検証後に元の `900` へ戻す（候補A が当たりなら `0` にするのが対処）。

並行して gnome-shell の JS リソースから `inhibit_remote_access` の呼び出し元を特定する。
`libgnome-shell.so` からは gresource を列挙できなかったので、リソースの所在の再探索が必要。

候補A が当たれば **gsettings 1行で解決**する。

### 4.3 P0-3: app_id の付与

判定には xdp のログメッセージ `Assigning app ID "%s" to pid %ld which has unit "%s"` を使う。

1. `systemd-run --user --scope --unit=app-rustdesk-probe` の中で `rustdesk --server` を起動
   （`app-rustdesk-probe.scope` は 2.5 の正規表現にマッチし app_id = `rustdesk` になる想定）
2. 接続して journal に上記メッセージが出るか
3. `flatpak permissions screencast` にエントリが増えるか
4. **2回目の接続でダイアログが出ないか** ← 本命の判定

通れば恒久化を検討する。RustDesk の root service が server を spawn する構造のため、
恒久化には `rustdesk.service` の変更かユーザユニット化が必要。そこは別途判断する。

### 4.4 Phase 0 の出口

| P0-2 | P0-1 | P0-3 | 結論 |
|---|---|---|---|
| 通る | — | 直る | **コード不要**。設定変更のみで完了 |
| 通る | 直る | — | **コード不要**。gsettings のみで完了 |
| 通る | 直らない | 直らない | **Phase 1 を実装**（層3で UI を消す） |
| 通らない | 要因判明→解除可 | — | 解除してから再判定 |
| 通らない | 要因不明 | — | **Phase 1 も不可**。inhibit の発生源を gnome-shell 側で特定して無効化する方向へ切り替える（C は却下済み: 8.2） |

---

## 5. Phase 1 — 自作 ScreenCast バックエンド

### 5.1 中核: 「承認する代理人」ではなく「ポリシー駆動プロキシ」

impl backend は他バックエンドへ要求を転送する仕組みを持たない。素朴に作ると
「自動承認しない要求は拒否」になり、**RustDesk 以外の画面共有が一切使えなくなる**。

そこで条件を満たさない要求は **xdg-desktop-portal-gnome にそのまま中継**する。
impl のメソッドは `(u response, a{sv} results)` を返すだけなので、中継は素直な async
転送で済む。

```
                     ┌─ ポリシー合致 ─▶ Mutter を直接叩く（UI なし）
RustDesk ─▶ xdp ─▶ 自作デーモン ─┤
                     └─ 不合致/失敗 ─▶ xdp-gnome へ中継（従来のダイアログ）
```

副産物として**失敗時が安全側に倒れる**。Mutter が `Session creation inhibited` を返し
続けても拒否ではなく中継＝ダイアログに落とせる。

### 5.2 コンポーネント

| ファイル | 役割 | 依存 |
|---|---|---|
| `portal-autoapprove.py` | エントリポイント。引数解析と起動のみ | — |
| `portal_autoapprove/policy.py` | 承認か中継かを決める**純関数**。入力: app_id, プロセス状況, 設定 → 出力: 判定+理由 | なし |
| `portal_autoapprove/monitors.py` | `GetCurrentState` の結果 → 記録する connector 名を選ぶ**純関数** | なし |
| `portal_autoapprove/mutter.py` | Mutter ScreenCast の薄いラッパ | Gio |
| `portal_autoapprove/impl.py` | D-Bus 面（ScreenCast / Session / Request）とセッション状態機械 | Gio |
| `portal_autoapprove/proxy.py` | xdp-gnome への中継（メソッド転送 + Session/Request 転送） | Gio |
| `autoapprove.portal` | バックエンド宣言 | — |
| `portal-autoapprove-install.sh` | 設置・撤去・kill switch | — |
| `screencast-probe.py` | Phase 0 の探針。以後は診断ツールとして残す | Gio |
| `screencast-client-test.py` | フロントエンド側から一連の流れを叩く E2E テスト | Gio |

純関数を2つ切り出しているのは、**D-Bus を立てずに単体テストできる範囲を最大化する**ため。
`policy.py` と `monitors.py` は Gio を import しない。

### 5.3 データフロー（自動承認パス）

```
(1) xdp -> CreateSession(handle, session_handle, app_id="", options)
      自作: Session オブジェクトを session_handle に export、response=0

(2) xdp -> SelectSources(handle, session_handle, "", {types, cursor_mode,
                                                     persist_mode, restore_token})
      自作: ポリシー判定 -> 合致なら要求内容を記録して response=0
            cursor_mode は要求されたものをそのまま保持（勝手に変えない）

(3) xdp -> Start(handle, session_handle, "", parent_window, options)
      自作: org.gnome.Mutter.ScreenCast.CreateSession({})        -> Session/uN
            Session.RecordMonitor("Virtual-1", {cursor-mode: N}) -> Stream/uM
            Stream の PipeWireStreamAdded を購読 -> Session.Start()
            node_id 受領後:
              response=0,
              results={streams: [(node_id, {position, size, source_type: 1})]}

(4) RustDesk -> xdp.OpenPipeWireRemote()
      xdp が node_id に権限を張った fd を返す（自作は関与しない。2.3 参照）
```

`persist_mode` / `restore_token` は**受け取っても無視し、`restore_data` を返さない**。
ダイアログが原理的に出ないので永続化は不要で、いま壊れている層2に依存しないため。

公開プロパティは `AvailableSourceTypes = 1`（MONITOR のみ）、
`AvailableCursorModes = 7`、`version = 5`。WINDOW / VIRTUAL を名乗らないのは、
それらを自動承認する設計になっていないため。

### 5.4 画面選択ポリシー

`monitors.py` は `org.gnome.Mutter.DisplayConfig.GetCurrentState` の結果に対して:

1. 設定された優先 connector 名（既定 `Virtual-1`）が connected ならそれ
2. 無ければ論理モニタ列の先頭
3. それも無ければエラー → 中継にフォールバック

優先名は `--prefer-connector` で変更可能。ミラー中は DP-1 と同一内容、DP-1 消灯中も
Virtual-1 は生き続けるので接続が切れない。

### 5.5 承認ポリシーと監査

2.6 の通り要求元は同定できないため、**RustDesk が接続中かどうかで近似**する。
指標は接続時のみ起動する `rustdesk --cm`（Connection Manager）プロセスの存在。

全要求を journal に1行で記録する（`journalctl --user -u portal-autoapprove`）:

```
req=Start session=/…/session/1 app_id="" decision=approve reason=rustdesk-cm-running
  connector=Virtual-1 cursor_mode=2 node_id=57 elapsed=180ms
```

残る穴は「RustDesk 接続中に別アプリが要求すると通る」ことだけで、ログで事後に必ず
検出できる。

### 5.6 設定項目

systemd user unit の `ExecStart` に渡す。設定ファイルは持たない（既存スクリプトと同じ流儀）。

| オプション | 既定値 | 意味 |
|---|---|---|
| `--prefer-connector NAME` | `Virtual-1` | 優先して記録する connector 名。connected でなければ論理モニタ列の先頭にフォールバック |
| `--auto-approve-when MODE` | `rustdesk-connected` | 自動承認する条件。`rustdesk-connected` = `rustdesk --cm` 稼働中のみ / `always` = 常に / `never` = 常に中継（検証用） |
| `--retry-seconds N` | `10` | Mutter が `Session creation inhibited` を返したときのリトライ上限。超えたら中継 |
| `--fallback-backend NAME` | `gnome` | 中継先の D-Bus 名の末尾。`org.freedesktop.impl.portal.desktop.<NAME>` を呼ぶ |
| `--policy-grace-ms N` | `1500` | `rustdesk-connected` モードで判定が「中継」に倒れたとき、`--cm` の出現を待つ猶予。250ms 間隔で再判定する |

### 5.7 エラー処理

| 事象 | 挙動 |
|---|---|
| Mutter が `Session creation inhibited` | 500ms 間隔で最大 `--retry-seconds`（既定10秒）リトライ。RustDesk は接続時に uinput で入力を注入するため、ブランク由来の inhibit ならこの間に解除される見込み。超えたら xdp-gnome へ中継 |
| `Virtual-1` が無い | 5.4 のフォールバック → それも失敗なら中継 |
| Mutter セッションが外部要因で終了 | Mutter の `Session.Closed` を購読し、impl の `Session.Closed` を emit して frontend に伝播 |
| デーモンがクラッシュ | systemd user unit `Restart=on-failure`。次要求は D-Bus activation で再起動。activation 自体が失敗すれば xdp は `portals.conf` のフォールバック `gnome` を使う → **ダイアログに戻る＝安全側** |
| ポリシー不合致 | xdp-gnome へ中継（従来通りダイアログ） |

### 5.8 設置

`autoapprove.portal` を `/usr/share/xdg-desktop-portal/portals/` に置く（**root が一度だけ
必要**）。書式は実機の `gnome.portal` に合わせる:

```
[portal]
DBusName=org.freedesktop.impl.portal.desktop.autoapprove
Interfaces=org.freedesktop.impl.portal.ScreenCast;
```

D-Bus activation（`~/.local/share/dbus-1/services/…autoapprove.service`）と
systemd user unit（`~/.config/systemd/user/portal-autoapprove.service`、
`Type=dbus` + `BusName=`）は `xdg-desktop-portal-gnome` の実物と同じ形にする。

バックエンドの選択:

```
~/.config/xdg-desktop-portal/gnome-portals.conf
  [preferred]
  default=gnome;gtk;
  org.freedesktop.impl.portal.Secret=gnome-keyring;
  org.freedesktop.impl.portal.ScreenCast=autoapprove;gnome;
```

`ScreenCast` の値は「自作 → 失敗時は gnome」というフォールバック順。他のポータルは
一切触らない。

### 5.9 kill switch

`~/.config/xdg-desktop-portal/gnome-portals.conf` を削除して
`systemctl --user restart xdg-desktop-portal` で完全に元通りになる。デーモンや
スクリプトを消す必要はない。`portal-autoapprove-install.sh --uninstall` で全撤去。

---

## 6. セキュリティ上の考慮

画面キャプチャの承認は**画面内の全情報を渡す操作**であり、自動承認はその判断を人間から
外すことを意味する。以下で範囲を限定する。

- **承認範囲**: RustDesk 接続中のみ。それ以外は従来のダイアログに委譲する（5.1）
- **残存リスク**: RustDesk 接続中に別アプリが要求すると通る。要求元を同定できない
  （2.6）ため原理的に閉じられない
- **監査**: 全要求を判定理由付きで journal に記録する（5.5）。「いつ・どの経路で画面が
  取られたか」は事後に必ず追える
- **範囲の限定**: 差し替えるのは `org.freedesktop.impl.portal.ScreenCast` のみ。
  FileChooser / Secret / RemoteDesktop 等は GNOME のまま
- **即時停止**: kill switch で1ファイル削除＋再起動で元通り（5.9）

---

## 7. テスト

- **単体**（`python3 -m unittest`、D-Bus 不要）
  - `policy.py`: 判定表（app_id の有無 × `--cm` の有無 × 設定モード）
  - `monitors.py`: connector 選択。`GetCurrentState` の実データを fixture 化し、
    ミラー時 / DP-1 消灯時 / Virtual-1 欠落時の3ケース
- **E2E**（`screencast-client-test.py`）: フロントエンド API を直接叩き、
  **ダイアログが出ないこと**と node_id が返ることを確認。RustDesk 不要で回せる
- **実機確認**
  - [ ] RustDesk で連続2回接続してダイアログが出ない
  - [ ] DP-1 消灯中に接続でき 3840x2160 で見える
  - [ ] デーモンを停止すると**ダイアログが出る**（フォールバックが効く）
  - [ ] kill switch で完全復旧する
  - [ ] 別アプリ（Chrome 等）ではダイアログが出る（中継が効く）

---

## 8. 検討した他の案

B / C / D / Flatpak 化のすべてを却下した。C は 2026-09-06 に実機で試した上での却下。

### 8.1 B. ダイアログを自動クリック（AT-SPI）

既存経路をそのまま使い、監視デーモンがダイアログを検出して「共有」を押す。実装は軽い
（100〜200行）が却下:

- `org.gnome.desktop.interface toolkit-accessibility` が現在 `false`。有効化が前提になる
- 日本語ラベル・フォーカス・タイミングに依存し、GNOME 更新で壊れる
- **層1の inhibit も xdp-gnome の SEGV も解決しない**（押せてもセッションが作れない）

### 8.2 C. gnome-remote-desktop（RDP）へ乗り換え — 実測で却下

ポータルを通らないので**ダイアログが構造的に存在しない**。46.3 が既にインストール済みで
コード不要。Tailscale 上（`100.123.232.87`）なので接続性の問題もない。当初は保留（Phase 0 で
層1が塞がったままなら再検討）としていたが、**2026-09-06 に実機で試して却下**した。

**却下理由: 同じユーザ名の既存セッションを終了しないと利用できない。**
gnome-remote-desktop の RDP は手元でログイン中のセッションにアタッチせず別セッションを
作るため、既存セッションと衝突する。

これは本構成の目的と根本的に相容れない。VKMS 構成は「物理モニタが消えても**同じセッションが
生き続ける**」ことを狙っているのに対し、RDP は別セッションを立てる。前提が違う。
RustDesk の ID 接続・中継・ファイル転送を失う点は、この時点では議論するまでもない。

### 8.3 D. xdg-desktop-portal-gnome にパッチを当てて自動承認ビルド

パッケージ更新のたびに再ビルドが必要で保守コストが見合わない。

### 8.4 RustDesk を Flatpak 版に入れ替える

「Flatpak なら `app_id` が実在するのでバックエンド実装が楽になるのでは」という案。
効果は本物だが、この構成では割に合わない。

**効果**: `app_id = com.rustdesk.RustDesk` が付き、(a) 承認ポリシーが
ヒューリスティックではなく厳密一致で書ける、(b) restore token がアプリ単位で正しく
スコープされる（2.5 の問題が解消する）。

**却下理由**:

- **直接原因は app_id ではなく層1の inhibit**（2.4）。Flatpak でも Mutter の inhibit は消えない
- RustDesk の無人アクセスは **root の system service に依存**している（boot 時自動起動・
  ログイン前アクセス・uinput 入力注入）。Flatpak は system service をインストールできない。
  **app_id が付く条件＝ユーザアプリとして `app-*.scope` で動くこと**であり、これは無人
  アクセスが要求する条件の正反対
- Flatpak で uinput を使うには `--device=all` が必要で権限の綱渡りが増える
- **Flatpak 化せずに同じ利益を得られる可能性がある**。xdp の導出はユーザ unit 名が
  `app-` で始まることしか見ていない（2.5）ので、ユーザ側 server を `app-rustdesk-*.scope`
  で動かせば `app_id=rustdesk` が付く。これが P0-3（4.3）である

---

## 9. 未確定事項（実装時に検証する）

1. **`rustdesk --cm` の起動順序**。ScreenCast 要求より先に `--cm` が起動するかは未確認。
   Phase 1 の最初のタスクとして、要求時点のプロセス一覧をログに出して確かめる。
   順序が逆なら `--auto-approve-when always`（＋監査ログ）に切り替える
2. **`portals.conf` の実効ファイル名**。`XDG_CURRENT_DESKTOP=ubuntu:GNOME` なので xdp は
   各設定ディレクトリで `ubuntu-portals.conf` → `gnome-portals.conf` → `portals.conf` の順に
   探す。どれが実際に読まれるかは journal で確認して確定する
3. **inhibit の要因**（4.2）。特定できれば Phase 1 が不要になる可能性がある。
   逆に S0（通常状態）でも通らなければ inhibit は恒久的で、**Phase 1 を含む全案が成立しない**。
   その場合は gnome-shell 側の発生源特定が唯一の道になる
4. **`app-*.scope` での app_id 付与**（4.3）が実際に効くか
5. **xdp-gnome の SEGV**（2.4）。Phase 1 では該当経路を通らなくなるため実害は消えるが、
   中継パス（5.1）では依然通るので、再現条件を記録しておく

---

## 10. 制約

- **要求元プロセスは同定できない**（2.6）。承認ポリシーは文脈の近似にとどまる
- **`.portal` の設置に root が一度だけ必要**。それ以外はすべてユーザ権限で完結する
- **GNOME 依存**。`org.gnome.Mutter.ScreenCast` を直接使うため、他コンポジタでは
  `mutter.py` の差し替えが必要になる。ポータルの impl 側インターフェース（`impl.py` /
  `proxy.py`）はコンポジタ非依存
- **Mutter ScreenCast の API 変更に追随が必要**。現在 `Version = 4`。メジャー更新時は
  `screencast-probe.py` で先に確認する
