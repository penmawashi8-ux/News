"""
YouTube動画アップロードモジュール
YouTube Data API v3 を使って動画をアップロードする。

認証フロー:
  1. 初回: client_secrets.json でブラウザOAuth2認証 → token.json 保存
  2. 以降: token.json の自動リフレッシュ
  3. GitHub Actions: SecretsからBase64デコードしてファイルを復元
"""

import base64
import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from src.utils.logger import get_logger

logger = get_logger(__name__)

# YouTube Data API v3 のスコープ
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# デフォルトファイルパス（環境変数で上書き可能）
DEFAULT_CLIENT_SECRETS_PATH = "client_secrets.json"
DEFAULT_TOKEN_PATH = "token.json"

# アップロード設定
CATEGORY_ID = "17"          # YouTube カテゴリID: スポーツ
PRIVACY_STATUS = "public"   # 公開設定: public / private / unlisted


def _get_client_secrets_path() -> str:
    """client_secrets.json のパスを環境変数から取得する"""
    return os.getenv("YOUTUBE_CLIENT_SECRETS_PATH", DEFAULT_CLIENT_SECRETS_PATH)


def _get_token_path() -> str:
    """token.json のパスを環境変数から取得する"""
    return os.getenv("YOUTUBE_TOKEN_PATH", DEFAULT_TOKEN_PATH)


def _restore_credentials_from_env() -> bool:
    """
    GitHub Actions 環境でSecretsからBase64デコードして認証ファイルを復元する。

    環境変数:
      YOUTUBE_CLIENT_SECRETS: client_secrets.jsonのBase64エンコード文字列
      YOUTUBE_TOKEN: token.jsonのBase64エンコード文字列

    Returns:
        True: 復元成功 / False: 環境変数が未設定
    """
    client_secrets_b64 = os.getenv("YOUTUBE_CLIENT_SECRETS")
    token_b64 = os.getenv("YOUTUBE_TOKEN")

    restored = False

    if client_secrets_b64:
        client_secrets_path = _get_client_secrets_path()
        try:
            decoded = base64.b64decode(client_secrets_b64).decode("utf-8")
            # JSONの妥当性確認
            json.loads(decoded)
            with open(client_secrets_path, "w", encoding="utf-8") as f:
                f.write(decoded)
            logger.info(f"[INFO] client_secrets.json を環境変数から復元: {client_secrets_path}")
            restored = True
        except Exception as e:
            logger.error(f"[ERROR] client_secrets.json の復元に失敗: {e}")

    if token_b64:
        token_path = _get_token_path()
        try:
            decoded = base64.b64decode(token_b64).decode("utf-8")
            json.loads(decoded)
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(decoded)
            logger.info(f"[INFO] token.json を環境変数から復元: {token_path}")
            restored = True
        except Exception as e:
            logger.error(f"[ERROR] token.json の復元に失敗: {e}")

    return restored


def authenticate() -> Credentials:
    """
    YouTube APIの認証を行い、認証情報を返す。

    初回実行時はブラウザが起動してOAuth認証を行い、
    token.json に認証情報を保存する。
    2回目以降は token.json から認証情報を読み込み、
    必要に応じてトークンをリフレッシュする。

    Returns:
        認証済みのCredentialsオブジェクト

    Raises:
        FileNotFoundError: client_secrets.json が見つからない場合
        RuntimeError: 認証に失敗した場合
    """
    # GitHub Actions環境の場合はSecretsから認証ファイルを復元
    _restore_credentials_from_env()

    token_path = _get_token_path()
    client_secrets_path = _get_client_secrets_path()

    creds = None

    # 既存のtoken.jsonを読み込む
    if Path(token_path).exists():
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
            logger.info("[INFO] token.json から認証情報を読み込みました")
        except Exception as e:
            logger.warning(f"[WARNING] token.json の読み込みに失敗: {e}")
            creds = None

    # トークンが無効または期限切れの場合はリフレッシュ or 再認証
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # トークンをリフレッシュ
            try:
                creds.refresh(Request())
                logger.info("[INFO] アクセストークンをリフレッシュしました")
            except Exception as e:
                logger.warning(f"[WARNING] トークンリフレッシュに失敗、再認証します: {e}")
                creds = None

        if not creds:
            # client_secrets.json の確認
            if not Path(client_secrets_path).exists():
                raise FileNotFoundError(
                    f"client_secrets.json が見つかりません: {client_secrets_path}\n"
                    "Google Cloud Console から OAuth2.0 クライアントIDを作成してください。"
                )

            # ブラウザで認証（初回のみ）
            logger.info("[INFO] ブラウザでYouTube API認証を行います...")
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, SCOPES)
            creds = flow.run_local_server(port=0)

        # token.json に保存
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        logger.info(f"[INFO] 認証情報を token.json に保存しました: {token_path}")

    return creds


def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
) -> str:
    """
    YouTube に動画をアップロードしてvideo_idを返す。

    Args:
        video_path: アップロードする動画ファイルのパス
        title: 動画のタイトル
        description: 動画の説明
        tags: タグのリスト

    Returns:
        アップロードされた動画のvideo_id

    Raises:
        FileNotFoundError: 動画ファイルが見つからない場合
        RuntimeError: アップロードに失敗した場合
    """
    if not Path(video_path).exists():
        raise FileNotFoundError(f"動画ファイルが見つかりません: {video_path}")

    logger.info(f"[INFO] YouTube動画アップロード開始: {title}")

    # 認証
    creds = authenticate()
    youtube = build("youtube", "v3", credentials=creds)

    # 動画メタデータ
    body = {
        "snippet": {
            "title": title[:100],          # タイトルは100文字以内
            "description": description[:5000],  # 説明は5000文字以内
            "tags": tags[:500],             # タグは最大500個
            "categoryId": CATEGORY_ID,
            "defaultLanguage": "ja",
            "defaultAudioLanguage": "ja",
        },
        "status": {
            "privacyStatus": PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False,
        },
    }

    # 動画ファイルのアップロード設定
    media = MediaFileUpload(
        video_path,
        chunksize=-1,           # 一括アップロード
        resumable=True,         # 中断・再開可能なアップロード
        mimetype="video/mp4",
    )

    try:
        # アップロードリクエスト
        request = youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                logger.info(f"[INFO] アップロード進捗: {progress}%")

        video_id = response["id"]
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info(f"[INFO] アップロード完了: {video_url}")
        return video_id

    except HttpError as e:
        error_details = json.loads(e.content.decode("utf-8"))
        error_message = error_details.get("error", {}).get("message", str(e))
        logger.error(f"[ERROR] YouTube APIエラー: {error_message}")
        raise RuntimeError(f"YouTube APIエラー: {error_message}")


def upload_thumbnail(video_id: str, thumbnail_path: str) -> bool:
    """
    YouTube動画にサムネイル画像を設定する。
    youtube.upload スコープのみでは権限不足となるため、失敗時はスキップして警告のみ出す。
    フル権限（youtube スコープ）のトークンがある場合に動作する。

    Args:
        video_id: サムネイルを設定する動画のID
        thumbnail_path: サムネイル画像のパス（PNG/JPEG）

    Returns:
        True: 設定成功 / False: 失敗またはスキップ
    """
    if not Path(thumbnail_path).exists():
        logger.warning(f"[WARNING] サムネイルファイルが見つかりません: {thumbnail_path}")
        return False

    try:
        creds = authenticate()
        youtube = build("youtube", "v3", credentials=creds)
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path, mimetype="image/png"),
        ).execute()
        logger.info(f"[INFO] サムネイル設定完了: video_id={video_id}")
        return True
    except HttpError as e:
        if e.status_code in (400, 403):
            logger.warning(
                "[WARNING] サムネイル設定失敗（権限不足）。"
                "YouTubeスタジオで手動設定してください: "
                f"https://studio.youtube.com/video/{video_id}/edit"
            )
        else:
            logger.warning(f"[WARNING] サムネイル設定失敗: {e}")
        return False
    except Exception as e:
        logger.warning(f"[WARNING] サムネイル設定失敗: {e}")
        return False


def build_jra_title(date_str: str) -> str:
    """
    JRA動画のタイトルを生成する。

    Args:
        date_str: 日付文字列（例: "04/12"）

    Returns:
        動画タイトル
    """
    return f"【{date_str}】本日のJRA制裁情報＆ニュース #Shorts"


def build_jra_sanctions_title(date_str: str, venue: str = "") -> str:
    """JRA制裁情報動画のタイトルを生成する"""
    venue_str = f" {venue}" if venue else ""
    return f"【{date_str}{venue_str}】本日のJRA制裁情報まとめ #Shorts"


def build_jra_news_title(date_str: str, venue: str = "") -> str:
    """JRA今日の出来事動画のタイトルを生成する"""
    venue_str = f" {venue}" if venue else ""
    return f"【{date_str}{venue_str}】本日のJRA今日の出来事 #Shorts"


def build_nar_title(date_str: str) -> str:
    """
    NAR動画のタイトルを生成する。

    Args:
        date_str: 日付文字列（例: "04/12"）

    Returns:
        動画タイトル
    """
    return f"【{date_str}】本日の地方競馬制裁情報＆ニュース #Shorts"


def build_jra_description(script: str) -> str:
    """JRA動画の説明文を生成する"""
    return (
        f"{script}\n\n"
        "#競馬 #JRA #制裁情報 #競馬ニュース #Shorts\n\n"
        "毎週土日・祝日の22時にJRA最新情報をお届けします！\n"
        "チャンネル登録・高評価よろしくお願いします！"
    )


def build_jra_sanctions_description(script: str) -> str:
    """JRA制裁情報動画の説明文を生成する"""
    return (
        f"{script}\n\n"
        "#競馬 #JRA #制裁情報 #騎手 #Shorts\n\n"
        "毎週土日・祝日の22時にJRA最新情報をお届けします！\n"
        "チャンネル登録・高評価よろしくお願いします！"
    )


def build_jra_news_description(script: str) -> str:
    """JRA今日の出来事動画の説明文を生成する"""
    return (
        f"{script}\n\n"
        "#競馬 #JRA #今日の出来事 #競馬ニュース #Shorts\n\n"
        "毎週土日・祝日の22時にJRA最新情報をお届けします！\n"
        "チャンネル登録・高評価よろしくお願いします！"
    )


def build_nar_description(script: str) -> str:
    """NAR動画の説明文を生成する"""
    return (
        f"{script}\n\n"
        "#競馬 #地方競馬 #NAR #制裁情報 #Shorts\n\n"
        "毎日22時に地方競馬最新情報をお届けします！\n"
        "チャンネル登録・高評価よろしくお願いします！"
    )


JRA_TAGS = ["競馬", "JRA", "制裁情報", "競馬ニュース", "Shorts", "ショート", "日本中央競馬会"]
JRA_SANCTIONS_TAGS = ["競馬", "JRA", "制裁情報", "騎手", "Shorts", "ショート", "日本中央競馬会"]
JRA_NEWS_TAGS = ["競馬", "JRA", "今日の出来事", "競馬ニュース", "Shorts", "ショート", "日本中央競馬会"]
NAR_TAGS = ["競馬", "地方競馬", "NAR", "制裁情報", "競馬ニュース", "Shorts", "ショート", "地方競馬全国協会"]


if __name__ == "__main__":
    # テスト実行（実際のアップロードは行わない）
    from dotenv import load_dotenv
    load_dotenv()

    print("=== YouTube アップローダー テスト ===")

    # 認証テスト（client_secrets.jsonが必要）
    client_secrets_path = _get_client_secrets_path()
    if Path(client_secrets_path).exists():
        print(f"client_secrets.json: 存在する")
        try:
            creds = authenticate()
            print(f"認証: 成功")
        except Exception as e:
            print(f"認証: 失敗 - {e}")
    else:
        print(f"client_secrets.json: 見つかりません（{client_secrets_path}）")
        print("Google Cloud Console でOAuth2.0クライアントIDを作成してください。")

    print(f"\nJRAタイトル例: {build_jra_title('04/12')}")
    print(f"NARタイトル例: {build_nar_title('04/12')}")

    print("\n✅ ステップ8完了: youtube_uploader.py テスト成功")
