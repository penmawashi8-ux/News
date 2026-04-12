# 競馬 YouTube Shorts 自動投稿システム

JRA（日本中央競馬会）および NAR（地方競馬全国協会）の当日情報を自動収集し、YouTube Shorts 動画を生成・投稿するシステムです。

---

## 1. システム概要

### 投稿スケジュール

| 項目 | JRA動画 | 地方競馬動画 |
|------|---------|------------|
| 投稿頻度 | 土・日・JRA開催祝日のみ | 毎日（地方競馬開催日） |
| 投稿時刻 | 22:00（JST） | 22:00（JST） |
| 内容 | JRAの制裁情報＋当日の出来事 | NARの制裁情報＋当日の出来事 |
| チャンネル | 同一チャンネルに別動画として投稿 | 同上 |

### 処理フロー

```
開催日判定 → スクレイピング → AI原稿生成 → VOICEVOX音声合成 → FFmpeg動画生成 → YouTube投稿
```

### 動画仕様

- 解像度: **1080×1920**（縦型・YouTube Shorts 必須）
- フレームレート: 30fps
- 音声: VOICEVOX（ずんだもん）+ BGM（音量20%）
- 字幕: NotoSansJP-Bold 52px・白・黒縁・下部焼き込み
- 動画長: 原稿の読み上げ時間に合わせて自動調整（約60秒）

---

## 2. ローカル環境セットアップ（Windows）

### 前提条件

```
Python 3.10 以上
FFmpeg
VOICEVOX
```

### セットアップ手順

**1. Python 3.10以上のインストール**

[python.org](https://www.python.org/downloads/) から Python 3.10+ をインストール。

**2. FFmpegのインストール**

```powershell
winget install ffmpeg
```

インストール後、コマンドプロンプトで確認：

```bash
ffmpeg -version
```

**3. VOICEVOXのダウンロード・起動**

1. [VOICEVOX公式サイト](https://voicevox.hiroshiba.jp/) からダウンロード
2. インストールして起動（タスクトレイに常駐させる）
3. デフォルトで `http://localhost:50021` でAPIが有効になる

**4. Pythonパッケージのインストール**

```bash
pip install -r requirements.txt
```

**5. 環境変数ファイルの設定**

```bash
# .env.example を .env にコピー
copy .env.example .env
```

`.env` ファイルを編集して各値を設定：

```env
ANTHROPIC_API_KEY=your_anthropic_api_key_here
VOICEVOX_URL=http://localhost:50021
VOICEVOX_SPEAKER_ID=3
```

**6. アセットファイルの配置**

```
assets/
├── backgrounds/
│   ├── jra/   ← JRA用背景画像（1080x1920以上のJPG/PNG）を配置
│   └── nar/   ← NAR用背景画像（1080x1920以上のJPG/PNG）を配置
├── bgm/       ← BGMファイル（MP3/WAV）を配置（任意）
└── fonts/
    └── NotoSansJP-Bold.ttf  ← フォントファイルを配置
```

NotoSansJP フォントのダウンロード: [Google Fonts](https://fonts.google.com/noto/specimen/Noto+Sans+JP)

---

## 3. YouTube API認証の取得手順

### Google Cloud Console での設定

**1. プロジェクトの作成**

1. [Google Cloud Console](https://console.cloud.google.com/) にアクセス
2. 新しいプロジェクトを作成（例: `keiba-shorts`）

**2. YouTube Data API v3 の有効化**

1. 「APIとサービス」→「ライブラリ」を開く
2. 「YouTube Data API v3」を検索して有効化

**3. OAuth 2.0 クライアントIDの作成**

1. 「APIとサービス」→「認証情報」→「認証情報を作成」→「OAuthクライアントID」
2. アプリケーションの種類: **デスクトップアプリ**
3. 名前を入力して「作成」をクリック

**4. client_secrets.json の配置**

1. 作成した認証情報の「JSONをダウンロード」をクリック
2. ダウンロードしたファイルをプロジェクトルートに `client_secrets.json` として配置

**5. 初回認証の実行**

```bash
python -c "from src.uploader.youtube_uploader import authenticate; authenticate()"
```

ブラウザが起動するので、YouTubeチャンネルを持つGoogleアカウントでログインして認証を許可。

**6. token.json の確認**

認証成功後、プロジェクトルートに `token.json` が生成されることを確認。

---

## 4. GitHub Actions設定

### Secrets の登録

**PowerShellでBase64エンコード:**

```powershell
# client_secrets.json をエンコードしてクリップボードにコピー
[Convert]::ToBase64String([IO.File]::ReadAllBytes("client_secrets.json")) | clip

# token.json をエンコードしてクリップボードにコピー
[Convert]::ToBase64String([IO.File]::ReadAllBytes("token.json")) | clip
```

**GitHub に登録する Secrets:**

| Secret名 | 内容 |
|---------|------|
| `ANTHROPIC_API_KEY` | Anthropic API Key |
| `YOUTUBE_CLIENT_SECRETS` | `client_secrets.json` の Base64エンコード文字列 |
| `YOUTUBE_TOKEN` | `token.json` の Base64エンコード文字列 |

**登録手順:**

1. GitHubリポジトリの「Settings」→「Secrets and variables」→「Actions」を開く
2. 「New repository secret」をクリック
3. 上記3つのSecretを登録

### ワークフロー

| ファイル | トリガー | 説明 |
|---------|---------|------|
| `.github/workflows/jra_post.yml` | 土日22:00 JST | JRA動画の生成・投稿 |
| `.github/workflows/nar_post.yml` | 毎日22:00 JST | NAR動画の生成・投稿 |

---

## 5. ローカルテスト実行

```bash
# JRA（投稿なし・ドライラン）
python main_jra.py --dry-run

# NAR（投稿なし・ドライラン）
python main_nar.py --dry-run

# JRA（開催日判定スキップ・ドライラン）
python main_jra.py --force --dry-run

# NAR（開催日判定スキップ・ドライラン）
python main_nar.py --force --dry-run

# JRA（実際に投稿）
python main_jra.py

# NAR（実際に投稿）
python main_nar.py
```

### 各モジュールの単体テスト

```bash
# 開催日判定
python -m src.utils.calendar

# JRAスクレイパー
python -m src.scraper.jra_scraper

# NARスクレイパー
python -m src.scraper.nar_scraper

# AI原稿生成
python -m src.ai.script_generator

# VOICEVOX TTS
python -m src.tts.voicevox_tts

# 動画生成（FFmpegの確認）
python -m src.video.video_builder

# YouTubeアップローダー（認証確認）
python -m src.uploader.youtube_uploader
```

---

## 6. トラブルシューティング

### VOICEVOXに繋がらない

```
[WARNING] VOICEVOXエンジンに接続できません: http://localhost:50021
```

**対処法:**
- VOICEVOXアプリが起動しているか確認（タスクトレイを確認）
- `http://localhost:50021/version` をブラウザで開いてAPIが応答するか確認
- ファイアウォールでポート50021が遮断されていないか確認
- `.env` の `VOICEVOX_URL` が正しいか確認

### YouTube認証エラー

```
[ERROR] YouTube APIエラー: ...
```

**対処法:**
1. `token.json` を削除して再認証

   ```bash
   del token.json
   python -c "from src.uploader.youtube_uploader import authenticate; authenticate()"
   ```

2. client_secrets.json が正しいか確認
3. Google Cloud Console で YouTube Data API v3 が有効化されているか確認
4. OAuthクライアントIDの種類が「デスクトップアプリ」になっているか確認

### スクレイピングが取れない

```
[ERROR] HTMLの取得に失敗しました
```

**対処法:**
- ネットワーク接続を確認
- JRA/NARのサイト構造が変わった可能性があります（Issueに報告してください）
- User-Agentが弾かれている場合は `HEADERS` を更新してください

### FFmpegエラー

```
[ERROR] FFmpeg実行失敗
```

**対処法:**
- `ffmpeg -version` でFFmpegがインストール済みか確認
- Windowsでは `winget install ffmpeg` を再実行
- PATHにFFmpegが通っているか確認（再起動が必要な場合あり）

### Anthropic APIエラー

```
[ERROR] JRA原稿生成に失敗
```

**対処法:**
- `.env` の `ANTHROPIC_API_KEY` が正しいか確認
- APIの使用制限（レートリミット）に達していないか確認
- フォールバック原稿が自動的に使用されます（ログに `WARNING` が出ます）

---

## 7. ディレクトリ構成

```
.
├── .github/
│   └── workflows/
│       ├── jra_post.yml     # JRA自動投稿ワークフロー
│       └── nar_post.yml     # NAR自動投稿ワークフロー
├── src/
│   ├── scraper/
│   │   ├── jra_scraper.py   # JRA制裁情報・ニュース取得
│   │   └── nar_scraper.py   # NAR制裁情報・ニュース取得
│   ├── ai/
│   │   └── script_generator.py  # Claude AIによる原稿生成
│   ├── tts/
│   │   └── voicevox_tts.py  # VOICEVOX音声合成
│   ├── video/
│   │   └── video_builder.py # FFmpeg動画生成
│   ├── uploader/
│   │   └── youtube_uploader.py  # YouTube API投稿
│   └── utils/
│       ├── calendar.py      # 開催日判定
│       └── logger.py        # ロギング設定
├── assets/
│   ├── backgrounds/
│   │   ├── jra/             # JRA用背景画像（gitignore済み）
│   │   └── nar/             # NAR用背景画像（gitignore済み）
│   ├── bgm/                 # BGMファイル（gitignore済み）
│   └── fonts/               # フォントファイル（gitignore済み）
├── output/                  # 生成動画の一時保存先（gitignore済み）
├── main_jra.py              # JRAエントリーポイント
├── main_nar.py              # NARエントリーポイント
├── requirements.txt
├── .env.example
└── README.md
```

---

## 8. 注意事項・ライセンス

- JRA・NAR各サイトの利用規約を遵守してください。スクレイピングの可否は各サービスの利用規約に従ってください。
- サーバー負荷軽減のため、リクエスト間に1秒以上の待機を設けています。
- 背景画像・BGMは著作権フリーのものを使用してください。
- `client_secrets.json` と `token.json` は絶対にコミットしないでください（.gitignore で除外済み）。
