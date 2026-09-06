## ツール選択ルール

このプロジェクトには4つのコード解析ツールが入っている。質問の種類で使い分ける

better-code-review-graph はアクション型の統合ツール構成。`query` / `review` / `security` に `action` パラメータを渡して使う

| 質問の種類 | 使うツール | 例 |
|-----------|-----------|-----|
| 意味・目的でコードを探す | better-code-review-graph `query`（`action=search`） | 「認証してる処理ある？」「エラーハンドリングどこ？」 |
| 変更の影響範囲 | better-code-review-graph `query`（`action=impact`） | 「この変更どこに影響する？」「blast radiusは？」 |
| 呼び出し元・依存の追跡 | better-code-review-graph `query`（`action=query`、pattern=callers_of 等） | 「verifyTokenの呼び出し元は？」「何に依存してる？」 |
| 定義元・実装クラス（行番号つき） | Serena `find_symbol` で位置を出し、必要なら `find_implementations` | 「NotificationChannelの定義元は？」「実装クラス一覧」 |
| 参照先の一覧（行番号つき） | Serena `find_symbol` → `find_referencing_symbols` | 「getConnectionを参照してるファイルは？」 |
| リネーム・シンボル編集 | Serena `rename_symbol` / `replace_symbol_body` | 「関数名を変えたい」「メソッドの中身を書き換えて」 |
| リポジトリ全体の構造・横断質問 | `/graphify query` | 「全体構成は？」「設計書とコードの関係は？」 |
| コード変更のレビュー | better-code-review-graph `review` | 「直近の変更をレビューして」 |
| セキュリティスキャン | better-code-review-graph `security`（`action=scan`） | 「脆弱性ない？」「セキュリティチェックして」 |

### 競合回避

- **「〜の処理ある？」「〜してるコードどこ？」にSerenaの `find_symbol` を使わない**。意味での検索は better-code-review-graph の `query`（`action=search`）を最初に使う
- Serena は **シンボル名が特定できている質問**（定義元・参照先・実装クラス・リネーム）にだけ使う
- **code-review-graph のツールは質問に使わない**。担当は pre-commit フックでレビュー用のコンテキストを供給すること
- 検索・影響範囲・レビューは code-review-graph と better-code-review-graph で機能が重複するが、**better-code-review-graph 側を使う**（重複する範囲ではセマンティック検索が強化されているため）
- Grep / Glob / Read はグラフツールで見つからないときの最終手段

### フォールバック

better-code-review-graph の `query`（`action=search`）が0件 → `/graphify query` で広域検索 → Serena `find_symbol`（部分一致）→ Grep / Glob / Read


