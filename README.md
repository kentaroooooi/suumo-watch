# suumo-watch

SUUMO の検索結果を 1 時間おきに取得し、新着物件を LINE に通知する。

## セットアップ

### 1. LINE Messaging API
1. https://developers.line.biz/console/ で Provider → Messaging API チャネルを作成
2. チャネル設定 → Messaging API タブ → 「チャネルアクセストークン（長期）」を発行 → `LINE_CHANNEL_ACCESS_TOKEN`
3. チャネル基本設定 → 「あなたのユーザーID」(U で始まる) → `LINE_USER_ID`
4. 同タブの QR コードから、自分の LINE でその公式アカウントを友だち追加

### 2. GitHub Secrets（リポジトリ → Settings → Secrets and variables → Actions）
| Name | Value |
|---|---|
| `SUUMO_URL` | 監視する検索結果 URL（PC 版・新着順） |
| `LINE_CHANNEL_ACCESS_TOKEN` | 上記 2 |
| `LINE_USER_ID` | 上記 3 |

### 3. 動作確認
Actions → SUUMO watch → Run workflow。初回は「監視を開始しました」が LINE に届く。

## 注意
- SUUMO の利用規約上、自動取得は推奨されていない。間隔は 1 時間、個人利用に留めること。
- HTML 構造が変わると取得できなくなる。その場合は LINE に警告が届く。
