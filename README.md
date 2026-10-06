# suumo-watch

SUUMO / LIFULL HOME'S / Yahoo!不動産 の検索結果を 1 時間おきに取得し、新着物件を LINE に通知する。

## セットアップ

### 1. LINE Messaging API
1. https://developers.line.biz/console/ で Provider → Messaging API チャネルを作成
2. チャネル設定 → Messaging API タブ → 「チャネルアクセストークン（長期）」を発行 → `LINE_CHANNEL_ACCESS_TOKEN`
3. 同タブの QR コードから、自分の LINE でその公式アカウントを友だち追加（ブロードキャスト方式なのでユーザー ID は不要）

### 2. GitHub Secrets（リポジトリ → Settings → Secrets and variables → Actions）
| Name | Value | 必須 |
|---|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | 上記 2 | ○ |
| `SUUMO_URL` | SUUMO の検索結果 URL（PC 版・新着順） | 1 つ以上 |
| `HOMES_URL` | LIFULL HOME'S の検索結果 URL（新着順） | 任意 |
| `YAHOO_URL` | Yahoo!不動産 の検索結果 URL（新着順） | 任意 |

- 未設定のサービスはスキップされる。追加したいときは Secret を足すだけ。
- 検索条件はサービスごとに同じ内容で絞り込み、並び順を「新着順」にして URL をコピーする。

### 3. 動作確認
Actions → SUUMO watch → Run workflow。サービスを追加した初回は「◯◯ の監視を開始しました」が LINE に届く。

## 仕組み
- `state.json` に `サービス:物件ID` 形式で既知物件を記録し、差分だけ通知する
- 複数サービスの新着は 1 通にまとめる（`[SUUMO]` `[HOME'S]` `[Yahoo!不動産]` のタグ付き）
- サービス間は 20〜40 秒ずらして取得。403 / 429 はリトライせず次回に回す
- 取得失敗の警告はサービスごとに 24 時間に 1 回まで

## 注意
- 各サイトの利用規約上、自動取得は推奨されていない。間隔は 1 時間、個人利用に留めること。
- HTML 構造が変わると取得できなくなる。その場合は LINE に警告が届く。
- 同じ物件が複数サイトに載ることがあり、現状はサイト横断の重複除去はしていない。
