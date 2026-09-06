# wayland-virtual-compositor-solution

物理モニタの電源を切っている状態でも Wayland (GNOME/Mutter) 上の RustDesk リモートデスクトップがリモートからの新規接続を既存のデスクトップセッションへと問題なく受け入れるUXを実現するための構成一式。VKMS の仮想モニタと、画面共有ダイアログを無人で自動承認する自作 xdg-desktop-portal バックエンドの2つで構成される。

- **状態**: 両機能とも完成・実機確認済み（2026-09-06）
- **運用手順・トラブルシュート**: [handoff-vkms-rustdesk.md](handoff-vkms-rustdesk.md)
- **設計の経緯・調査記録**: [docs/design-history.md](docs/design-history.md)

---

## これは何をするものか

Wayland はコンポジタが実際の出力に直接描画するため、物理モニタの電源を切って
connector が `disconnected` になると、その上で動くリモートデスクトップセッションも
道連れで死ぬ。これを2段階で解決する。

1. **VKMS 仮想モニタ (`Virtual-1`)** をカーネルの DRM レイヤに常設し、物理モニタ
   (`DP-1`) と常時ミラーにする。物理モニタが消えても Virtual-1 は `connected` の
   ままなので、RustDesk はそちらにキャプチャを続けられる。
2. **画面共有ダイアログの自動承認 (`portal-autoapprove`)**。RustDesk 接続のたびに
   出る GNOME の承認ダイアログは無人環境では押す人間がいない。xdg-desktop-portal の
   ScreenCast バックエンドを自作に差し替え、`rustdesk --cm` が動いている（＝実際に
   接続中である）ことを条件にダイアログ無しで承認する。

```
        ┌─────────────────────────────────┐
        │   GNOME / Mutter (Wayland)      │
        └───────────┬─────────┬───────────┘
                    │         │  ← 常時ミラー
          ┌─────────▼──┐   ┌──▼──────────┐
          │  DP-1      │   │  Virtual-1  │
          │  (物理 4K) │   │  (VKMS)     │
          └────────────┘   └──────┬──────┘
             電源OFFで消える       │ 常に connected
                                   ▼
                    ┌───────────────────────────┐
                    │ xdg-desktop-portal         │
                    │  └─ portal-autoapprove ────┼─→ 承認ダイアログ無しで
                    │      (rustdesk --cm 検出)  │   RustDesk がキャプチャ
                    └───────────────────────────┘
```

設計判断の理由（なぜミラーか、なぜ EDID 注入ではなく `video=` か、なぜポータルの
バックエンドごと差し替えたか等）は [docs/design-history.md](docs/design-history.md) に記録している。

## 動作環境

Ubuntu Desktop 24.04 / GNOME (mutter 46.2) / Wayland / NVIDIA GPU
(`nvidia-drm modeset=1`) / DisplayPort モニタ / RustDesk。

DP 接続前提（電源 OFF で HPD が落ち connector が disconnected になる挙動に依存）。
GNOME 以外のコンポジタでは表示設定周りの手順が変わる。詳細は
[docs/design-history.md 7章](docs/design-history.md#7-制約と他環境へ持っていく場合の注意)。

## ディレクトリ構成

```
.
├── vkms-apply.sh              # VKMS 仮想モニタの永続化設定（要 root・初回のみ）
├── vkms-verify.sh              # 起動後の検証（root 不要）
├── vkms-probe.sh                # EDID 経路の調査用（参考。EDID 注入は不可能と確定済み）
├── mirror-apply.py             # DP-1 + Virtual-1 のミラー構成を Mutter に適用
├── portal-autoapprove.py       # ScreenCast ポータルの自作バックエンド本体（デーモン）
├── portal-autoapprove-install.sh  # 上の設置/撤去/状態確認
├── portal_autoapprove/         # バックエンドの実装（impl / policy / mutter / proxy 等）
├── install/autoapprove.portal  # xdg-desktop-portal 向けバックエンド宣言ファイル
├── screencast-probe.py         # Mutter に直接セッションを張れるか確かめる探針
├── screencast-client-test.py   # ポータルを叩く E2E テスト（RustDesk 不要）
├── tests/                      # portal_autoapprove の単体テスト
├── handoff-vkms-rustdesk.md    # 運用者向け手順・トラブルシュート
└── docs/
    ├── design-history.md       # 設計・構築の経緯（旧 README.md）
    └── superpowers/            # 設計書・調査記録・実装プラン
```

## セットアップ

初回構築の手順。既に構築済みの環境では不要（[handoff-vkms-rustdesk.md](handoff-vkms-rustdesk.md) を参照）。

### 1. VKMS 仮想モニタを永続化する（要 root・要再起動）

```bash
sudo bash vkms-apply.sh
# 再起動後
bash vkms-verify.sh
```

`/etc/default/grub` に `video=Virtual-1:3840x2160@60` を追加し、`vkms` モジュールを
起動時ロード対象にする。EDID を使わずカーネルコマンドラインで解像度を直接注入する
方式（理由は [docs/design-history.md 3章](docs/design-history.md#3-構築の経緯)以降を参照）。

### 2. ミラー構成を適用する

```bash
python3 mirror-apply.py --show     # 現在の構成を確認
python3 mirror-apply.py            # DP-1 + Virtual-1 のミラーを適用（永続化）
```

GNOME設定アプリの「ミラー」UIはリフレッシュレート完全一致のモードしか候補に
出さないため使えない。D-Bus (`ApplyMonitorsConfig`) を直接叩く。

DP-1 消灯中に単独で 4K を維持したい場合（`monitors.xml` に組み合わせ別の構成を
保存しておく必要があるため）:

```bash
python3 mirror-apply.py --virtual-only   # DP-1 を消した状態で実行
```

### 3. 画面共有ダイアログの自動承認を設置する

```bash
gsettings set org.gnome.desktop.session idle-delay 0   # アイドルブランクによる inhibit を止める（前提条件）
sudo bash portal-autoapprove-install.sh --install
bash portal-autoapprove-install.sh --status
```

既定では `rustdesk --cm`（Connection Manager、接続中のみ存在するプロセス）が
動いている間だけ自動承認する。`--auto-approve-when always` にすると常時承認になる
（`portal-autoapprove.py --help` 参照）。

撤去（kill switch）:

```bash
bash portal-autoapprove-install.sh --uninstall
# sudo が使えない場合:
rm -f ~/.config/xdg-desktop-portal/*portals.conf && systemctl --user restart xdg-desktop-portal.service
```

## テスト

```bash
python3 -m unittest discover -s tests
```

実機に触らず `portal_autoapprove` のセッション/リクエストのライフサイクルや
承認ポリシーを検証する。実機確認用のツールは別にある:

```bash
python3 screencast-probe.py --show          # モニタ構成の確認
python3 screencast-client-test.py           # ポータル経由の E2E テスト（RustDesk 不要）
```

## 制約・既知のリスク

- **`rustdesk --cm` の検出は「文脈の近似」であって「要求元の同定」ではない**。
  RustDesk 接続中に別プロセスが画面キャプチャを要求しても承認されてしまう。
  ポータルの impl backend からは要求元プロセスを厳密に特定できないため構造的に
  避けられない。詳細は [docs/design-history.md 5章](docs/design-history.md#5-画面共有ダイアログの自動承認)。
- **設置中はウィンドウ単位の画面共有がマシン全体で使えなくなる**（モニタ全体の共有は可）。
- **vkms の preferred モードは 1024x768 固定**。DP-1 消灯時の4Kは `monitors.xml` の
  保存済み構成に依存する。`monitors.xml` を消すと再発する。
- **この kernel の vkms は EDID を一切扱えない**（`nm -u vkms.ko` で確認済み）。
  EDID コピーの方向で解決しようとしないこと。

## ドキュメント一覧

| ドキュメント | 内容 |
|---|---|
| [handoff-vkms-rustdesk.md](handoff-vkms-rustdesk.md) | 運用者向け手順・ログの見方・トラブルシュート |
| [docs/design-history.md](docs/design-history.md) | 発端の問題分析、設計判断、試行錯誤の全記録（旧 README.md） |
| [docs/superpowers/specs/2026-09-05-portal-screencast-autoapprove-design.md](docs/superpowers/specs/2026-09-05-portal-screencast-autoapprove-design.md) | 自動承認機能の設計書 |
| [docs/superpowers/findings/2026-09-06-inhibit-investigation.md](docs/superpowers/findings/2026-09-06-inhibit-investigation.md) | inhibit 問題の調査記録 |
| [docs/superpowers/plans/2026-09-06-portal-screencast-autoapprove.md](docs/superpowers/plans/2026-09-06-portal-screencast-autoapprove.md) | 実装プラン |
