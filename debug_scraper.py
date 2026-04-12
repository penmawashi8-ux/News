"""
スクレイピングデバッグスクリプト
各サイトのHTML構造を確認してスクレイパーの修正に役立てる。

実行方法:
  python debug_scraper.py
"""

import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import pytz

JST = pytz.timezone("Asia/Tokyo")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        r.encoding = r.apparent_encoding
        return r.text
    except Exception as e:
        print(f"  [ERROR] {e}")
        return None


def show_links(soup, keyword=""):
    """指定キーワードを含むリンクを表示"""
    links = soup.find_all("a", href=True)
    matched = [a for a in links if keyword.lower() in a["href"].lower()] if keyword else links
    for a in matched[:20]:
        print(f"  href={a['href']!r:60s}  text={a.get_text(strip=True)[:40]!r}")


def show_tables(soup):
    """テーブル構造を表示"""
    tables = soup.find_all("table")
    print(f"  テーブル数: {len(tables)}")
    for i, t in enumerate(tables[:3]):
        rows = t.find_all("tr")
        print(f"\n  [テーブル{i}] {len(rows)}行")
        for j, row in enumerate(rows[:5]):
            cells = [c.get_text(strip=True)[:30] for c in row.find_all(["td","th"])]
            print(f"    row{j}: {cells}")


def show_articles(soup):
    """article/li要素を表示"""
    items = soup.find_all(["article", "li"])
    print(f"  article/li要素数: {len(items)}")
    for item in items[:10]:
        text = item.get_text(strip=True)[:80]
        cls = item.get("class", [])
        print(f"  <{item.name} class={cls}> {text!r}")


def show_divs_with_class(soup, keywords):
    """特定キーワードを含むclass名のdivを表示"""
    for kw in keywords:
        found = soup.find_all(True, class_=lambda c: c and kw in " ".join(c).lower())
        if found:
            print(f"\n  class に '{kw}' を含む要素: {len(found)}個")
            for el in found[:5]:
                print(f"    <{el.name} class={el.get('class')}> {el.get_text(strip=True)[:60]!r}")


# ══════════════════════════════════════════
print("=" * 60)
print("① jockey-sanction.com の構造確認")
print("=" * 60)
html = fetch("https://jockey-sanction.com")
if html:
    soup = BeautifulSoup(html, "lxml")
    print(f"\n<title>: {soup.title.string if soup.title else 'なし'}")

    print("\n▼ テーブル構造")
    show_tables(soup)

    print("\n▼ article/li 要素 (先頭10件)")
    show_articles(soup)

    print("\n▼ 制裁関連キーワードを含む class の要素")
    show_divs_with_class(soup, ["sanction", "entry", "post", "article", "content", "list"])

    print("\n▼ 全リンク (先頭20件)")
    show_links(soup)

time.sleep(1)

# ══════════════════════════════════════════
print("\n" + "=" * 60)
print("② jra.go.jp/news/ の構造確認")
print("=" * 60)
html = fetch("https://www.jra.go.jp/news/")
if html:
    soup = BeautifulSoup(html, "lxml")
    print(f"\n<title>: {soup.title.string if soup.title else 'なし'}")

    today = datetime.now(JST)
    date_prefix = today.strftime("%Y%m") + "/" + today.strftime("%m%d")
    print(f"\n当日の日付プレフィックス: {date_prefix}")

    print("\n▼ /news/ を含むリンク")
    show_links(soup, "/news/")

    print("\n▼ テーブル構造")
    show_tables(soup)

    print("\n▼ article/li 要素 (先頭10件)")
    show_articles(soup)

    print("\n▼ news/list/article 関連 class の要素")
    show_divs_with_class(soup, ["news", "list", "article", "content", "entry"])

time.sleep(1)

# ══════════════════════════════════════════
print("\n" + "=" * 60)
print("③ JRA ニュース記事ページの構造確認（今日の例）")
print("=" * 60)
today = datetime.now(JST)
# 今日のURL候補を試す
for seq in range(1, 5):
    test_url = (
        f"https://www.jra.go.jp/news/"
        f"{today.strftime('%Y%m')}/"
        f"{today.strftime('%m%d')}{seq:02d}.html"
    )
    print(f"\n試行: {test_url}")
    html = fetch(test_url)
    if html:
        soup = BeautifulSoup(html, "lxml")
        print(f"  <title>: {soup.title.string if soup.title else 'なし'}")
        h1 = soup.find("h1")
        print(f"  <h1>: {h1.get_text(strip=True) if h1 else 'なし'}")
        # 本文を含みそうな要素を表示
        for tag in ["article", "main", ".content", ".newsDetail", "#content"]:
            if tag.startswith(".") or tag.startswith("#"):
                el = soup.select_one(tag)
            else:
                el = soup.find(tag)
            if el:
                print(f"  <{tag}> 発見: {el.get_text(strip=True)[:150]!r}")
        show_divs_with_class(soup, ["content", "article", "news", "detail", "body"])
        break
    time.sleep(1)

print("\n完了。この出力をそのままコピーして貼り付けてください。")
