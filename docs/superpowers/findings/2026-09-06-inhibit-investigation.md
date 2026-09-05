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
| S1 DP-1 電源 OFF | モニタの電源ボタン | | | |
| S2 アイドルブランク後 | `idle-delay` を 60 にして1分放置 | | | |
| S3 S1 + S2 | 電源 OFF のまま1分放置 | | | |

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

## app_id 検証 (P0-3)

| 確認項目 | 結果 |
|---|---|
| journal に `Assigning app ID` が出るか | |
| `flatpak permissions screencast` にエントリが増えるか | |
| 2回目の接続でダイアログが出ないか | |

## 結論

（調査完了後に記入）
