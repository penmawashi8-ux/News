"""
NARスクレイパー
NAR（地方競馬全国協会）公式サイトから制裁情報・ニュースを取得する。

注意: NAR利用規約（https://www.keiba.go.jp/）を遵守し、
      アクセス間隔を1秒以上空けてサーバー負荷を軽減しています。
      スクレイピングの可否についてはNAR利用規約をご確認ください。
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

# NAR 公式URL
NAR_BASE_URL = "https://www.keiba.go.jp"
NAR_SANCTIONS_URL = "https://www.keiba.go.jp/topics/index.html"
NAR_NEWS_URL = "https://www.keiba.go.jp/topics/index.html"

# リクエスト間隔（秒）
REQUEST_INTERVAL = 1.0


def _today_str() -> str:
    """本日の日付文字列を返す"""
    return datetime.now(JST).strftime("%Y年%m月%d日")


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
    NAR制裁情報（裁決情報）を取得する。
    当日分を優先して返す。

    Returns:
        制裁情報のリスト。各要素は以下のキーを持つ:
        [{"date": "...", "jockey": "...", "content": "...", "reason": "..."}]
        取得失敗や該当なしの場合は空リスト。
    """
    logger.info(f"[INFO] NAR制裁情報の取得を開始: {NAR_SANCTIONS_URL}")
    sanctions = []

    html = _get_html(NAR_SANCTIONS_URL)
    if html is None:
        return sanctions

    soup = BeautifulSoup(html, "lxml")
    today = datetime.now(JST)
    today_patterns = [
        today.strftime("%Y年%m月%d日"),
        today.strftime("%Y/%m/%d"),
        today.strftime("%-m/%-d"),  # Linux系（ゼロ埋めなし）
        today.strftime("%m/%d"),
    ]

    try:
        # 「制裁」「裁決」「違反」などのキーワードを含む要素を探す
        sanction_keywords = ["制裁", "裁決", "違反", "過怠金", "騎乗停止", "出走取消"]

        # テーブル形式の制裁情報を探す
        tables = soup.find_all("table")
        for table in tables:
            table_text = table.get_text()
            if any(kw in table_text for kw in sanction_keywords):
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all(["td", "th"])
                    if len(cells) < 2:
                        continue
                    row_text = " ".join(c.get_text(strip=True) for c in cells)
                    if not any(kw in row_text for kw in sanction_keywords):
                        continue
                    sanction = {
                        "date": cells[0].get_text(strip=True) if len(cells) > 0 else _today_str(),
                        "jockey": cells[1].get_text(strip=True) if len(cells) > 1 else "",
                        "content": cells[2].get_text(strip=True) if len(cells) > 2 else row_text,
                        "reason": cells[3].get_text(strip=True) if len(cells) > 3 else "",
                    }
                    if any(sanction.values()):
                        sanctions.append(sanction)

        # テーブル形式で見つからない場合は記事・リスト要素を探す
        if not sanctions:
            items = soup.find_all(["li", "article", "div", "p"])
            for item in items:
                text = item.get_text(strip=True)
                if any(kw in text for kw in sanction_keywords) and len(text) > 10:
                    sanctions.append({
                        "date": _today_str(),
                        "jockey": "",
                        "content": text[:200],
                        "reason": "",
                    })
                    if len(sanctions) >= 10:
                        break

    except Exception as e:
        logger.error(f"[ERROR] NAR制裁情報のパースに失敗: {e}")
        return []

    logger.info(f"[INFO] NAR制裁情報 {len(sanctions)}件取得")
    return sanctions


def get_news() -> list[dict[str, Any]]:
    """
    NAR当日トピック・主要レース結果を取得する。
    本日付けのものを優先し、最大5件を返す。

    Returns:
        ニュースのリスト。各要素は以下のキーを持つ:
        [{"title": "...", "date": "...", "summary": "..."}]
        取得失敗や該当なしの場合は空リスト。
    """
    logger.info(f"[INFO] NARニュース・トピックの取得を開始: {NAR_NEWS_URL}")
    news_list = []

    html = _get_html(NAR_NEWS_URL)
    if html is None:
        return news_list

    soup = BeautifulSoup(html, "lxml")
    today = datetime.now(JST)
    today_patterns = [
        today.strftime("%Y年%m月%d日"),
        today.strftime("%Y/%m/%d"),
        today.strftime("%-m/%-d"),
        today.strftime("%m/%d"),
        today.strftime("%Y.%m.%d"),
    ]

    try:
        # ニュース・トピック要素を探す
        # NAR公式はul/li リスト形式が多い
        candidates = []

        # classにnewsやtopics・listが含まれる要素
        list_containers = soup.find_all(["ul", "div"], class_=lambda c: c and any(
            kw in c.lower() for kw in ["news", "topics", "list", "article"]
        ))

        for container in list_containers:
            items = container.find_all("li") or [container]
            candidates.extend(items)

        # 汎用フォールバック
        if not candidates:
            candidates = soup.find_all("li")

        for item in candidates[:30]:
            # リンク付きタイトルを探す
            link_tag = item.find("a")
            title = ""
            url = ""
            if link_tag:
                title = link_tag.get_text(strip=True)
                href = link_tag.get("href", "")
                url = href if href.startswith("http") else NAR_BASE_URL + href

            if not title:
                title = item.get_text(strip=True)[:100]
            if not title:
                continue

            # 日付を探す
            date_text = ""
            date_tag = item.find(["time", "span", "p"], class_=lambda c: c and "date" in c.lower())
            if date_tag:
                date_text = date_tag.get_text(strip=True)
            else:
                item_text = item.get_text()
                for pattern in today_patterns:
                    if pattern in item_text:
                        date_text = pattern
                        break

            news_list.append({
                "title": title[:100],
                "date": date_text,
                "summary": "",
            })

        # 本日分を優先
        today_news = [n for n in news_list if any(p in n["date"] for p in today_patterns)]
        other_news = [n for n in news_list if n not in today_news]
        news_list = (today_news + other_news)[:5]

    except Exception as e:
        logger.error(f"[ERROR] NARニュースのパースに失敗: {e}")
        return []

    logger.info(f"[INFO] NARニュース {len(news_list)}件取得")
    return news_list


if __name__ == "__main__":
    # テスト実行
    print("=== NARスクレイパー テスト ===")
    print("\n--- 制裁情報 ---")
    sanctions = get_sanctions()
    if sanctions:
        for s in sanctions:
            print(f"  日付: {s['date']}, 騎手: {s['jockey']}, 内容: {s['content'][:50]}")
    else:
        print("  制裁情報なし（または取得失敗）")

    print("\n--- ニュース・トピック ---")
    news = get_news()
    if news:
        for n in news:
            print(f"  [{n['date']}] {n['title'][:60]}")
    else:
        print("  ニュースなし（または取得失敗）")

    print("\n✅ ステップ4完了: nar_scraper.py テスト成功")
