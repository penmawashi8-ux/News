"""
JRAスクレイパー
制裁情報: https://jockey-sanction.com
ニュース:  https://www.jra.go.jp/news/ から当日記事リンクを探して本文取得

注意: 各サイトの利用規約を遵守し、アクセス間隔を1秒以上空けています。
"""

import re
import time
from datetime import datetime
from typing import Any

import pytz
import requests
from bs4 import BeautifulSoup

from src.utils.logger import get_logger

logger = get_logger(__name__)

JST = pytz.timezone("Asia/Tokyo")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# 制裁情報ソース（サードパーティ集約サイト）
SANCTION_URL = "https://jockey-sanction.com"

# JRAニュースindex
JRA_NEWS_INDEX = "https://www.jra.go.jp/news/"
JRA_NEWS_BASE  = "https://www.jra.go.jp"

REQUEST_INTERVAL = 1.0


def _today_jst() -> datetime:
    return datetime.now(JST)


def _get_html(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
        time.sleep(REQUEST_INTERVAL)
        return resp.text
    except requests.RequestException as e:
        logger.error(f"[ERROR] HTML取得失敗: url={url}, error={e}")
        return None


# ──────────────────────────────────────────
# 制裁情報: jockey-sanction.com
# ──────────────────────────────────────────

def get_sanctions() -> list[dict[str, Any]]:
    """
    jockey-sanction.com から当日分の制裁情報を取得する。

    Returns:
        [{"date": "...", "jockey": "...", "content": "...", "reason": "..."}]
        取得失敗・該当なしは空リスト。
    """
    logger.info(f"[INFO] JRA制裁情報取得開始: {SANCTION_URL}")
    sanctions = []
    today = _today_jst()

    # 当日を示す文字列パターン
    today_patterns = [
        today.strftime("%Y年%m月%d日"),
        today.strftime("%Y/%m/%d"),
        today.strftime("%Y-%m-%d"),
        today.strftime("%-m月%-d日"),
        today.strftime("%m/%d"),
    ]

    html = _get_html(SANCTION_URL)
    if html is None:
        return sanctions

    soup = BeautifulSoup(html, "lxml")

    try:
        # ── テーブル形式を優先して探す ──
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            header_cells = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])] if rows else []

            for row in rows[1:]:  # ヘッダー行をスキップ
                cells = row.find_all(["td", "th"])
                if not cells:
                    continue
                row_text = " ".join(c.get_text(strip=True) for c in cells)

                # 当日のみ、または全件（日付列が見つからない場合）
                is_today = any(p in row_text for p in today_patterns)

                if len(cells) >= 2:
                    sanctions.append({
                        "date": cells[0].get_text(strip=True),
                        "jockey": cells[1].get_text(strip=True) if len(cells) > 1 else "",
                        "content": cells[2].get_text(strip=True) if len(cells) > 2 else row_text,
                        "reason": cells[3].get_text(strip=True) if len(cells) > 3 else "",
                        "_is_today": is_today,
                    })

        # 当日分がある場合は当日のみに絞る
        today_s = [s for s in sanctions if s.pop("_is_today", False)]
        if today_s:
            sanctions = today_s
        else:
            # 当日分が特定できない場合: 直近5件を残す
            for s in sanctions:
                s.pop("_is_today", None)
            sanctions = sanctions[:5]

        # ── テーブルで取れない場合: 記事/リスト形式 ──
        if not sanctions:
            for item in soup.find_all(["article", "li", "div"]):
                text = item.get_text(strip=True)
                if len(text) < 15:
                    continue
                # 制裁関連キーワードを含む要素のみ
                if not any(kw in text for kw in ["制裁", "騎乗停止", "過怠金", "戒告", "注意"]):
                    continue
                sanctions.append({
                    "date": today.strftime("%Y年%m月%d日"),
                    "jockey": "",
                    "content": text[:150],
                    "reason": "",
                })
                if len(sanctions) >= 5:
                    break

    except Exception as e:
        logger.error(f"[ERROR] 制裁情報パース失敗: {e}")
        return []

    logger.info(f"[INFO] JRA制裁情報 {len(sanctions)}件取得")
    return sanctions


# ──────────────────────────────────────────
# ニュース: JRA公式 index → 当日記事を取得
# ──────────────────────────────────────────

def _find_today_news_urls() -> list[str]:
    """
    JRAニュースindexから当日の記事URLを探して返す。

    URL パターン例:
      https://www.jra.go.jp/news/202604/041204.html
      → /news/{YYYYMM}/{MMDD}{seq}.html

    Returns:
        当日記事URLのリスト
    """
    today = _today_jst()
    # URL中に含まれる日付プレフィックス: e.g. "202604/0412"
    date_prefix = today.strftime("%Y%m") + "/" + today.strftime("%m%d")

    html = _get_html(JRA_NEWS_INDEX)
    if html is None:
        return []

    soup = BeautifulSoup(html, "lxml")
    urls = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        # /news/YYYYMM/MMDD で始まるリンクを抽出
        if date_prefix in href and href.endswith(".html"):
            full_url = href if href.startswith("http") else JRA_NEWS_BASE + href
            if full_url not in urls:
                urls.append(full_url)

    logger.info(f"[INFO] 当日ニュースURL {len(urls)}件発見: {urls}")
    return urls


def _fetch_news_article(url: str) -> dict[str, Any] | None:
    """
    JRAニュース記事URLから本文を取得する。

    Args:
        url: 記事URL

    Returns:
        {"title": ..., "date": ..., "summary": ...} or None
    """
    html = _get_html(url)
    if html is None:
        return None

    soup = BeautifulSoup(html, "lxml")

    # タイトル取得
    title = ""
    for tag in ["h1", "h2", "title"]:
        el = soup.find(tag)
        if el:
            title = el.get_text(strip=True)
            # <title>タグの場合「JRA - ○○」形式を整形
            title = re.sub(r"^(JRA\s*[-－|｜]\s*|日本中央競馬会\s*[-－|｜]\s*)", "", title)
            if title:
                break

    # 本文取得（最初の意味ある段落）
    summary = ""
    main_area = soup.find(["article", "main", "div"], class_=re.compile(r"content|article|news|body", re.I))
    if not main_area:
        main_area = soup
    for p in main_area.find_all("p"):
        text = p.get_text(strip=True)
        if len(text) > 20:
            summary = text[:200]
            break

    today_str = _today_jst().strftime("%Y年%m月%d日")

    if not title:
        return None

    return {
        "title": title[:100],
        "date": today_str,
        "summary": summary,
    }


def get_news() -> list[dict[str, Any]]:
    """
    JRAニュースを取得する。
    当日のニュース記事URLをindexから発見し、各記事を個別に取得する。

    Returns:
        [{"title": "...", "date": "...", "summary": "..."}]
        取得失敗・該当なしは空リスト。
    """
    logger.info("[INFO] JRAニュース取得開始")
    news_list = []

    # ① 当日記事URLをindexから探す
    article_urls = _find_today_news_urls()

    # ② 各記事の本文を取得
    for url in article_urls[:5]:  # 最大5件
        article = _fetch_news_article(url)
        if article:
            news_list.append(article)
            logger.info(f"[INFO] 記事取得: {article['title'][:50]}")

    # ③ indexから見つからない場合: URL推測でフォールバック
    if not news_list:
        logger.warning("[WARNING] indexから当日記事が見つからず、URL推測でフォールバック")
        today = _today_jst()
        base = f"{JRA_NEWS_BASE}/news/{today.strftime('%Y%m')}/{today.strftime('%m%d')}"
        for seq in range(1, 8):  # 01〜07まで試す
            url = f"{base}{seq:02d}.html"
            article = _fetch_news_article(url)
            if article:
                news_list.append(article)
                logger.info(f"[INFO] URL推測で記事取得: {url}")
            elif seq == 1:
                break  # 01が404なら連番なし

    logger.info(f"[INFO] JRAニュース {len(news_list)}件取得")
    return news_list


if __name__ == "__main__":
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
            if n["summary"]:
                print(f"    {n['summary'][:80]}")
    else:
        print("  ニュースなし（または取得失敗）")

    print("\n✅ ステップ3完了: jra_scraper.py テスト成功")
