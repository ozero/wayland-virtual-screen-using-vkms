# inhibit 要因の調査記録

`screencast-probe.py` を4状態で実行し、Mutter が `Session creation inhibited` を
返す条件を特定する。設計書 4.2 / 実装計画 Task 3 に対応。

発端は 2026-09-04 20:11〜20:15 のログで、RustDesk 接続時に

```
xdg-desktop-portal-gnome: Error restoring stream from session:
  GDBus.Error:org.freedesktop.DBus.Error.Failed: Session creation inhibited
```

が出ていたこと。承認を自動化しても Mutter がセッションを作らない状態では意味がないため、
ポータル層に手を入れる前にここを切り分ける。

## 環境（調査時点）

| 項目 | 値 |
|---|---|
| `idle-delay` | `uint32 900`（15分でブランク） |
| `lock-enabled` | `false`（ロック画面は最初から除外） |
| 構成 | Virtual-1 + DP-1 が (0,0) 3840x2160 のミラー |

## 結果

| 状態 | 作り方 | 時刻 | probe の結果 | exit |
|---|---|---|---|---|
| S0 通常 | 何もしない | 01:46:23 | `成功: node_id=75` | 0 |
| S1 DP-1 電源 OFF | モニタの電源ボタン | 01:47:11 | `成功: node_id=75` | 0 |
| S2 アイドルブランク後 | （S3 と同時に測定。下記参照） | — | — | — |
| S3 S1 + S2 | 電源 OFF のまま `idle-delay` 60 で放置 | 01:52 | **`失敗: セッション作成が inhibit されている`** | 2 |

### S0 の詳細

```
connectors: Virtual-1, DP-1
  Virtual-1    position=(0, 0) size=3840x2160
  DP-1         position=(0, 0) size=3840x2160

記録対象: Virtual-1 position=(0, 0) size=3840x2160
成功: node_id=75
```

**S0 が成功した時点で「inhibit は恒久的ではない」ことが確定した。**
9月4日の inhibit には何らかのトリガ条件がある。

### S1 の詳細

```
/sys/class/drm/card0-Virtual-1/status    connected
/sys/class/drm/card1-DP-1/status         disconnected

connectors: Virtual-1
  Virtual-1    position=(0, 0) size=3840x2160

記録対象: Virtual-1 position=(0, 0) size=3840x2160
成功: node_id=75
```

**物理モニタの電源 OFF は inhibit のトリガではない。**
DP-1 の connector が消えても Virtual-1 は 3840x2160 を維持しており
（`monitors.xml` の `{Virtual-1}` 単独構成が効いている）、ScreenCast も通る。

### S3 の詳細 — **inhibit の再現に成功**

`idle-delay` を一時的に 60 秒にして放置し、`ScreenSaver.GetActive` が `true`
（＝画面ブランク／スクリーンシールドが立った状態）になった時点で探針を実行した。

```
--- 条件成立 t=20s idle=64ms ScreenSaver.GetActive=(true,) ---
記録対象: Virtual-1 position=(0, 0) size=3840x2160
失敗: セッション作成が inhibit されている
  GDBus.Error:org.freedesktop.DBus.Error.Failed: Session creation inhibited
exit=2
=== 探針直後の状態 ===
idle=(uint64 143073,) active=(true,)
```

## 結論: アイドル画面ブランクが inhibit の原因

| 状態 | DP-1 | 画面ブランク | 結果 |
|---|---|---|---|
| S0 | 点灯 | なし | 成功 |
| S1 | **消灯** | なし | 成功 |
| S3 | 消灯 | **あり** | **inhibit** |

S1 と S3 の差分はブランクの有無だけなので、**トリガは gnome-shell のアイドル画面ブランク
（スクリーンシールド）**で確定。`lock-enabled=false` でもシールドは立つため、ロックを
無効にしていても発生する。

これが 2026-09-04 に RustDesk の restore が失敗していた理由である。無人運用のマシンは
接続と接続のあいだ必ずアイドルになるので、**毎回ダイアログが出る**という症状と整合する。

## 対処の検証: `idle-delay = 0`

画面を自動でブランクしなければシールドが立たず inhibit も起きないはず、という仮説を検証した。

```
--- idle-delay を 0 に設定 ---
t= 30s  active=(false,)
t= 60s  active=(false,)
t= 90s  active=(false,)
t=120s  active=(false,)
t=150s  active=(false,)
=== 探針 (idle-delay=0、長時間アイドル後) ===
記録対象: Virtual-1 position=(0, 0) size=3840x2160
成功: node_id=75
exit=0
最終状態: active=(false,) idle=(uint64 210359,)
```

210 秒アイドルでもシールドが立たず、ScreenCast セッションが作れた。**対処として有効。**

### 併せて確認した無人運用まわりの設定

| 設定 | 値 | 評価 |
|---|---|---|
| `org.gnome.desktop.session idle-delay` | **`0`**（変更した） | ブランクしない = inhibit しない |
| `org.gnome.desktop.screensaver lock-enabled` | `false` | ロック画面は出ない |
| `…power sleep-inactive-ac-type` | `'nothing'` | アイドルでサスペンドしない |
| logind `IdleAction` | `ignore` | 同上 |

いずれも無人リモートアクセスに対して安全側。変更したのは `idle-delay` のみ。

戻す場合: `gsettings set org.gnome.desktop.session idle-delay 900`

## app_id 検証 (P0-3) — 保留

設計書 2.5 で「RustDesk が system unit にいるため `app_id` が空文字列になり、restore token が
permission store に正しく保管されない」ことを指摘していた。ただし 2026-09-04 のログでは
`Error restoring stream from session` が出ており、**restore data 自体は見つかっていた**ので、
app_id の問題は inhibit による失敗の結果であって独立した原因ではない可能性が高い。

inhibit を解消した状態で RustDesk の通し確認を行い、それでもダイアログが出る場合にのみ
P0-3 を実施する。

| 確認項目 | 結果 |
|---|---|
| journal に `Assigning app ID` が出るか | 未実施（保留） |
| `flatpak permissions screencast` にエントリが増えるか | 未実施（保留） |
| 2回目の接続でダイアログが出ないか | **要確認（受け入れテスト）** |

## gnome-shell の呼び出し元特定 — 実施せず

計画では `inhibit_remote_access` を呼んでいる JS を gresource から特定する手順を置いていたが、
S1 と S3 の差分で「ブランクがトリガ」と確定できたため実施しなかった。呼び出し元の特定は
対処には不要（確認的な情報にとどまる）。

## 結論

**原因: gnome-shell のアイドル画面ブランク（スクリーンシールド）が remote access を
inhibit し、Mutter が ScreenCast セッションの作成を拒否していた。**

無人運用のマシンは接続と接続のあいだ必ずアイドルになるため、ほぼ毎回この状態になる。
xdg-desktop-portal-gnome は restore に失敗するとダイアログにフォールバックするので、
「RustDesk 接続のたびに毎回ダイアログが出る」という症状になっていた。

**対処: `gsettings set org.gnome.desktop.session idle-delay 0`（適用済み）**

設計書 4.4 の出口表では「S0 通る / P0-1 で直る」に該当し、**Phase 1（自作 ScreenCast
バックエンド）は不要**。実装計画の Task 4 以降は実施しない。

ただし最終判断は RustDesk での通し確認による。連続2回の接続でダイアログが出ないことを
確認してから、README と handoff に反映して完了とする。それでも出る場合は P0-3（app_id）に進む。


---

# 追記: Phase 1 の実機検証（2026-09-06 19:31）

`idle-delay=0` で層1(inhibit)を解消したあと、RustDesk での通し確認により層2(永続化)にも
我々には直せない原因が2つあることが判明したため、Phase 1（自作 ScreenCast バックエンド）を
実装した。設計書 8 章の A 案。

## 層2 の2つの原因

1. **restore token が物理モニタの EDID に紐づく。** ミラー構成では論理モニタが1つしかなく、
   xdg-desktop-portal-gnome はその識別子として DP-1 の EDID
   (`HPN:HP 27f 4k:3CM02743XR`) を記録する。Virtual-1 の識別子は `unknown:unknown:unknown`。
   DP-1 はモニタ電源 OFF で消えるため復元が必ず失敗し、毎回ダイアログに落ちる
2. **xdg-desktop-portal-gnome 46.2-0ubuntu1 が毎回 SIGSEGV する。**
   `g_object_unref` → `g_type_check_instance_is_fundamentally_a` の use-after-free。
   画面共有ダイアログを出した直後、セッションを閉じる経路で落ちる。パッケージのバグ

## 自作バックエンドでの結果

```
成功: node_id=88 position=(0, 0) size=(3840, 2160) source_type=1   exit=0
req=Start … decision=approve connector=Virtual-1 cursor_mode=2 node_id=88 elapsed=9ms
ダイアログの回数: 0
```

**DP-1 が `disconnected`（モニタ電源 OFF）の状態で、ダイアログ無しに 3840x2160 の
Virtual-1 のストリームが 9ms で返った。** 自作バックエンドは connector を名前で指定するため、
EDID にも論理モニタの都合にも左右されず、restore token も使わず、SEGV する経路も通らない。
