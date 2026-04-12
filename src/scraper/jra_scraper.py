"""
JRAスクレイパー
JRA公式サイトから制裁情報・ニュースを取得する。

注意: JRA利用規約（https://www.jra.go.jp/）を遵守し、
      アクセス間隔を1秒以上空けてサーバー負荷を軽減しています。
      スクレイピングの可否についてはJRA利用規約をご確認ください。
"""

import time
from datetime import datetime
from typing import Any

import pytz
import requests
from bs4 import BeautifulSoup

from src.utils.logger import get_logger

logger = get_logger(__name__)

# JSTタイムゾーン
JST = pytz.timezone("Asia/Tokyo")

# 共通HTTPヘッダー
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# JRA 公式URL
JRA_SANCTIONS_URL = "https://www.jra.go.jp/keiba/thisweek/saibansho/"
JRA_NEWS_URL = "https://www.jra.go.jp/news/"

# リクエスト間隔（秒）
REQUEST_INTERVAL = 1.0


def _today_str() -> str:
    """本日の日付文字列を返す（例: 2024/04/12 → '04/12' や '2024年4月12日'）"""
    now = datetime.now(JST)
    return now.strftime("%Y年%m月%d日")


def _get_html(url: str) -> str | None:
    """
    指定URLのHTMLを取得する。
    失敗した場合はNoneを返す。

    Args:
        url: 取得対象URL

    Returns:
        HTML文字列、またはNone
    """
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        time.sleep(REQUEST_INTERVAL)
        return response.text
    except requests.RequestException as e:
        logger.error(f"[ERROR] HTMLの取得に失敗しました: url={url}, error={e}")
        return None


def get_sanctions() -> list[dict[str, Any]]:
    """
    JRA裁決情報（制裁情報）を取得する。
    当日分のみを抽出して返す。

    Returns:
        制裁情報のリスト。各要素は以下のキーを持つ:
        [{"date": "...", "jockey": "...", "content": "...", "reason": "..."}]
        取得失敗や該当なしの場合は空リスト。
    """
    logger.info(f"[INFO] JRA制裁情報の取得を開始: {JRA_SANCTIONS_URL}")
    sanctions = []

    html = _get_html(JRA_SANCTIONS_URL)
    if html is None:
        return sanctions

    soup = BeautifulSoup(html, "lxml")
    today = datetime.now(JST)
    today_str_patterns = [
        today.strftime("%Y年%m月%d日"),
        today.strftime("%-m月%-d日"),  # Linux系（ゼロ埋めなし）
        today.strftime("%m月%d日"),    # ゼロ埋めあり
    ]

    try:
        # テーブルやリスト要素から制裁情報を探す
        # JRAのページ構造に合わせてパース
        tables = soup.find_all("table")

        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                if not cells:
                    continue

                row_text = " ".join(cell.get_text(strip=True) for cell in cells)

                # 本日の日付が含まれる行を抽出
                is_today = any(p in row_text for p in today_str_patterns)
                if not is_today and len(sanctions) == 0:
                    # 日付が見つからない場合は全件取得（ページ構造が変わった可能性）
                    pass

                if len(cells) >= 3:
                    sanction = {
                        "date": cells[0].get_text(strip=True) if len(cells) > 0 else "",
                        "jockey": cells[1].get_text(strip=True) if len(cells) > 1 else "",
                        "content": cells[2].get_text(strip=True) if len(cells) > 2 else "",
                        "reason": cells[3].get_text(strip=True) if len(cells) > 3 else "",
                    }
                    # 内容が空でなければ追加
                    if any(sanction.values()):
                        sanctions.append(sanction)

        # テーブル形式でなかった場合、divやリスト要素から取得
        if not sanctions:
            items = soup.find_all(["li", "div"], class_=lambda c: c and "sanction" in c.lower())
            for item in items:
                text = item.get_text(strip=True)
                if text:
                    sanctions.append({
                        "date": _today_str(),
                        "jockey": "",
                        "content": text,
                        "reason": "",
                    })

    except Exception as e:
        logger.error(f"[ERROR] JRA制裁情報のパースに失敗: {e}")
        return []

    logger.info(f"[INFO] JRA制裁情報 {len(sanctions)}件取得")
    return sanctions


def get_news() -> list[dict[str, Any]]:
    """
    JRAニュースを取得する。
    本日付けのものを優先し、最大5件を返す。

    Returns:
        ニュースのリスト。各要素は以下のキーを持つ:
        [{"title": "...", "date": "...", "summary": "..."}]
        取得失敗や該当なしの場合は空リスト。
    """
    logger.info(f"[INFO] JRAニュースの取得を開始: {JRA_NEWS_URL}")
    news_list = []

    html = _get_html(JRA_NEWS_URL)
    if html is None:
        return news_list

    soup = BeautifulSoup(html, "lxml")
    today = datetime.now(JST)
    today_patterns = [
        today.strftime("%Y年%m月%d日"),
        today.strftime("%Y/%m/%d"),
        today.strftime("%-m/%-d"),  # Linux系
        today.strftime("%m/%d"),
        today.strftime("%Y.%m.%d"),
    ]

    try:
        # ニュース記事要素を探す（ul/li 形式や article 形式）
        news_items = soup.find_all(["li", "article", "div"], class_=lambda c: c and (
            "news" in c.lower() or "article" in c.lower() or "list" in c.lower()
        ))

        # 汎用的なリスト取得（class無指定でもul/liから）
        if not news_items:
            news_items = soup.select("ul li, .news-list li, .newsList li")

        for item in news_items[:20]:  # 上限20件チェック
            title_tag = item.find(["a", "h2", "h3", "p"])
            title = title_tag.get_text(strip=True) if title_tag else item.get_text(strip=True)
            if not title:
                continue

            # 日付の取得
            date_text = ""
            date_tag = item.find(["time", "span", "p"], class_=lambda c: c and "date" in c.lower())
            if date_tag:
                date_text = date_tag.get_text(strip=True)
            else:
                # テキスト内の日付パターンを探す
                item_text = item.get_text()
                for pattern in today_patterns:
                    if pattern in item_text:
                        date_text = pattern
                        break

            # サマリー取得
            summary_tag = item.find("p")
            summary = summary_tag.get_text(strip=True) if summary_tag else ""

            news_list.append({
                "title": title[:100],   # タイトルは100文字以内
                "date": date_text,
                "summary": summary[:200],  # サマリーは200文字以内
            })

        # 本日分を優先（日付が今日のものを先頭に）
        today_news = [n for n in news_list if any(p in n["date"] for p in today_patterns)]
        other_news = [n for n in news_list if n not in today_news]
        news_list = (today_news + other_news)[:5]  # 最大5件

    except Exception as e:
        logger.error(f"[ERROR] JRAニュースのパースに失敗: {e}")
        return []

    logger.info(f"[INFO] JRAニュース {len(news_list)}件取得")
    return news_list


if __name__ == "__main__":
    # テスト実行
    print("=== JRAスクレイパー テスト ===")
    print("\n--- 制裁情報 ---")
    sanctions = get_sanctions()
    if sanctions:
        for s in sanctions:
            print(f"  日付: {s['date']}, 騎手: {s['jockey']}, 内容: {s['content'][:50]}")
    else:
        print("  制裁情報なし（または取得失敗）")

    print("\n--- ニュース ---")
    news = get_news()
    if news:
        for n in news:
            print(f"  [{n['date']}] {n['title'][:60]}")
    else:
        print("  ニュースなし（または取得失敗）")

    print("\n✅ ステップ3完了: jra_scraper.py テスト成功")
