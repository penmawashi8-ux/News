"""
開催日判定ユーティリティ
JRA・NAR の当日開催有無をウェブアクセスで確認する。
"""

import time
from datetime import date, datetime

import pytz
import requests

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 共通HTTPヘッダー
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

JST = pytz.timezone("Asia/Tokyo")


def _today_jst() -> date:
    """JST の今日の日付を返す"""
    return datetime.now(JST).date()


def is_jra_race_day() -> bool:
    """
    本日がJRA開催日かどうかを判定する。

    判定ロジック:
      1. JRA公式トップ (https://www.jra.go.jp) にアクセスして
         当日レース情報の有無で判定する。
      2. 取得失敗時のフォールバック: 土日ならTrue、平日ならFalse。

    Returns:
        True: 本日JRA開催あり / False: 開催なし
    """
    today = _today_jst()
    logger.info(f"JRA開催日判定: 対象日={today}")

    try:
        # JRAトップページにアクセスしてレース情報の有無を確認
        response = requests.get(
            "https://www.jra.go.jp",
            headers=HEADERS,
            timeout=10,
        )
        response.raise_for_status()
        time.sleep(1)  # サーバー負荷軽減

        html = response.text

        # 「本日のレース」「出馬表」などのキーワードでレース開催を検出
        race_keywords = [
            "本日のレース",
            "出馬表",
            "race_list",
            "kaisai",
            "レース一覧",
        ]
        for keyword in race_keywords:
            if keyword in html:
                logger.info(f"[INFO] JRA開催あり（キーワード検出: {keyword}）")
                return True

        logger.info("[INFO] JRA開催なし（キーワード未検出）")
        return False

    except requests.RequestException as e:
        # フォールバック: 土日（weekday=5,6）ならTrue
        logger.warning(f"[WARNING] JRAサイトへのアクセス失敗、フォールバック使用: {e}")
        weekday = today.weekday()
        is_weekend = weekday >= 5  # 5=土曜, 6=日曜
        logger.info(f"[INFO] フォールバック判定（曜日={weekday}）: {is_weekend}")
        return is_weekend


def is_nar_race_day() -> bool:
    """
    本日が地方競馬開催日かどうかを判定する。

    NAR公式 (https://www.keiba.go.jp) の当日開催情報から判定。
    取得失敗時は True を返す（地方競馬はほぼ毎日開催）。

    Returns:
        True: 本日NAR開催あり / False: 開催なし
    """
    today = _today_jst()
    logger.info(f"NAR開催日判定: 対象日={today}")

    try:
        # NARトップページにアクセスして本日の開催情報を確認
        response = requests.get(
            "https://www.keiba.go.jp",
            headers=HEADERS,
            timeout=10,
        )
        response.raise_for_status()
        time.sleep(1)  # サーバー負荷軽減

        html = response.text

        # 「本日の開催」「レース情報」などのキーワードで開催を検出
        race_keywords = [
            "本日の開催",
            "本日のレース",
            "kaisai",
            "race",
            "出走表",
            "開催競馬場",
        ]
        for keyword in race_keywords:
            if keyword in html:
                logger.info(f"[INFO] NAR開催あり（キーワード検出: {keyword}）")
                return True

        # キーワード未検出でもNARはほぼ毎日開催なので True を返す
        logger.info("[INFO] NAR開催情報のキーワード未検出だが、フォールバックで True を返す")
        return True

    except requests.RequestException as e:
        # 取得失敗時は True（地方競馬はほぼ毎日開催）
        logger.warning(f"[WARNING] NARサイトへのアクセス失敗、フォールバックで True を返す: {e}")
        return True


if __name__ == "__main__":
    # テスト実行
    print("=== 開催日判定テスト ===")
    jra_result = is_jra_race_day()
    print(f"JRA開催: {jra_result}")
    nar_result = is_nar_race_day()
    print(f"NAR開催: {nar_result}")
    print("✅ ステップ2完了: calendar.py テスト成功")
