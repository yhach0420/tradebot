# kabuステーション® API — Phase 1 接続確認

このページは、`scripts/kabu_api_check.py` でローカルの kabusapi に接続し、トークン取得・現在値・板の一部を取得するための準備です。

## 事前準備

1. **kabuステーション**を起動し、本体の設定で **API 機能を有効**にし、`APIパスワード` を決めます。
2. kabusapi は標準では **`http://localhost:18080/kabusapi`**（本番用）または `18081`（検証用）で待ち受けます。ファイアウォールなどでポートがブロックされていないことを確認してください。
3. プロジェクト直下の `.env.example` を参考に `.env` を作成し、`KABU_API_PASSWORD` に本体で設定した **APIパスワードだけ** を記載します。パスワードをソースコードへ直書きしないでください。

## 実行方法

プロジェクト直下で:

```bash
python scripts/kabu_api_check.py --symbol 9984
```

- 東証以外のときは `--exchange`（市場コード）を指定してください。詳細は [公式リファレンス](https://kabucom.github.io/kabusapi/ptal/reference.html) の「銘柄コード」の形式 `[銘柄コード]@[市場コード]` に従います。
- ベース URL を変える場合は環境変数 `KABU_API_BASE` または `--base-url` を指定します。

## 出力

| 種別 | パス |
|------|------|
| チェック結果 JSON | `results/kabu_api/YYYYMMDD/` 配下に `kabu_api_check_<銘柄>_<市場>_<HHMMSS>.json` |
| 実行ログ | `logs/runtime/kabu_api_check_YYYYMMDD.log` |

JSON には **API トークン全文は保存しません**。板情報は売気配・買気配の先頭数段だけを `board_excerpt` にまとめ、現在値関連は `current_quote` にまとめます。応答フィールド一覧は `_note_response_keys` にのみキー名を残します。

成功時ログに **CurrentPrice** が出れば、現在値取得成功とみなせます。

## トラブルシュート

| 現象 | 確認 |
|------|------|
| 接続できない (`Connection refused` など) | kabuステーション起動状態・APIオン・ポート 18080/18081 |
| 401 | APIパスワード誤り、またはログアウト／別トークン発行済みなど |
| 板の一部が null | 銘柄未登録の場合があります。公式ドキュメントの「API登録銘柄」に関する説明を参照してください |
