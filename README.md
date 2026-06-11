# calendar-watcher

指定したJSONエンドポイントを定期的にチェックし、内容に変化があった場合に
[ntfy.sh](https://ntfy.sh) 経由でプッシュ通知を送る汎用ツールです。

監視対象のURLや通知先は、すべてリポジトリのSecretsで設定します。
このリポジトリのコード・ログ・コミット履歴に監視対象の情報は含まれません。

## 必要なSecrets

| Secret名 | 内容 |
|---|---|
| `TARGET_URL_TEMPLATE` | 監視対象URL。日付部分は `{from}` `{to}` プレースホルダ |
| `NTFY_TOPIC` | ntfy.sh のトピック名 |
| `CLICK_URL` | 通知タップ時に開くURL(任意) |

## 動作

- GitHub Actions が10分間隔で `watcher.py` を実行
- 今日から4ヶ月先までを1ヶ月単位でチェック
- 前回との差分があり、かつデータが出現していれば最優先度で通知
- 状態は `state.json` に保存(ハッシュ値と最終チェック日のみ)

## 手動テスト

Actions タブ → watch → Run workflow で実行するとテスト通知が送られます。
