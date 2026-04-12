"""
AI原稿生成モジュール
Google Gemini API（無料）または Anthropic Claude を使って
JRA・NAR向けYouTube Shorts用ナレーション原稿を生成する。

優先順位:
  1. GEMINI_API_KEY が設定されていれば Gemini を使用（無料・推奨）
  2. ANTHROPIC_API_KEY が設定されていれば Anthropic Claude を使用
  3. どちらもなければフォールバックテンプレートを使用
"""

import os
from datetime import datetime
from typing import Any

import pytz

from src.utils.logger import get_logger

logger = get_logger(__name__)

JST = pytz.timezone("Asia/Tokyo")

# Gemini モデル候補（先頭から順に試す）
# gemini-1.5-flash は2026年時点で廃止済みのため新モデルを優先
GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash-002",
]

# Anthropic モデル（フォールバック用）
ANTHROPIC_MODEL = "claude-sonnet-4-20250514"

# 共通システムプロンプト
SYSTEM_PROMPT = """あなたは競馬情報をYouTube Shorts向けに読み上げ原稿へ変換するアシスタントです。
以下のルールを厳守してください：
- テンポよく、聞き取りやすい話し言葉で書く
- 難しい漢字にはひらがなを混ぜる（例：騎手→きしゅ、制裁→せいさい）
- 1文を短く（30文字以内）
- 数字は読みやすく（例：1着→いちちゃく、1番人気→いちばんにんき）
- 情報がない場合も自然につなげる
- 合計350文字程度（60秒以内で読めるボリューム）に収める"""


def _format_sanctions(sanctions: list[dict[str, Any]]) -> str:
    """
    制裁情報をプロンプト用テキストに変換する。
    競馬場・レース番号・騎手・馬名・制裁内容を1行で明示する。
    """
    if not sanctions:
        return "（制裁情報なし）"
    lines = []
    for s in sanctions[:5]:
        venue = s.get("venue", "")
        race = s.get("race", "")
        jockey = s.get("jockey", "")
        horse = s.get("horse", "")
        content = s.get("content", "")

        venue_race = f"{venue} {race}".strip()
        jockey_horse = jockey
        if horse:
            jockey_horse += f"（{horse}）"

        line = f"・{venue_race} / 騎手:{jockey_horse} / 制裁:{content}"
        lines.append(line)
    return "\n".join(lines)


def _format_news(news: list[dict[str, Any]]) -> str:
    """
    ニュース（今日の出来事）をAIプロンプト用テキストに変換する。
    summary は長すぎる場合1500文字に収める。
    """
    if not news:
        return "（ニュースなし）"
    lines = []
    for n in news[:2]:
        summary = n.get("summary", "")
        if summary:
            lines.append(summary[:1500])
    return "\n".join(lines) if lines else "（ニュースなし）"


def _generate_with_gemini(prompt: str) -> str | None:
    """
    Google Gemini API で原稿を生成する（無料枠使用）。

    Args:
        prompt: ユーザープロンプト

    Returns:
        生成されたテキスト、失敗時はNone
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)

        # 利用可能なモデルを順番に試す
        last_error = None
        for model_name in GEMINI_MODELS:
            try:
                model = genai.GenerativeModel(
                    model_name=model_name,
                    system_instruction=SYSTEM_PROMPT,
                )
                response = model.generate_content(prompt)
                text = response.text.strip()
                logger.info(f"[INFO] Gemini ({model_name}) で原稿生成完了 ({len(text)}文字)")
                return text
            except Exception as e:
                logger.warning(f"[WARNING] Gemini モデル {model_name} 失敗: {e}")
                last_error = e
                continue

        logger.error(f"[ERROR] 全Geminiモデルが失敗: {last_error}")
        return None

    except ImportError:
        logger.error("[ERROR] google-generativeai パッケージが未インストールです: pip install google-generativeai")
        return None
    except Exception as e:
        logger.error(f"[ERROR] Gemini API エラー: {e}")
        return None


def _generate_with_anthropic(prompt: str) -> str | None:
    """
    Anthropic Claude API で原稿を生成する。

    Args:
        prompt: ユーザープロンプト

    Returns:
        生成されたテキスト、失敗時はNone
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        logger.info(f"[INFO] Anthropic で原稿生成完了 ({len(text)}文字)")
        return text

    except ImportError:
        logger.error("[ERROR] anthropic パッケージが未インストールです")
        return None
    except Exception as e:
        logger.error(f"[ERROR] Anthropic API エラー: {e}")
        return None


def _call_ai(prompt: str) -> str | None:
    """
    利用可能なAI APIを順番に試して原稿を生成する。

    優先順位: Gemini（無料）→ Anthropic → None

    Args:
        prompt: ユーザープロンプト

    Returns:
        生成されたテキスト、全て失敗時はNone
    """
    # 1. Gemini（無料・優先）
    if os.getenv("GEMINI_API_KEY"):
        result = _generate_with_gemini(prompt)
        if result:
            return result

    # 2. Anthropic（フォールバック）
    if os.getenv("ANTHROPIC_API_KEY"):
        result = _generate_with_anthropic(prompt)
        if result:
            return result

    logger.warning(
        "[WARNING] AI API が利用できないためフォールバックテンプレートを使用します "
        f"(GEMINI_API_KEY={'設定済' if os.getenv('GEMINI_API_KEY') else '未設定'}, "
        f"ANTHROPIC_API_KEY={'設定済' if os.getenv('ANTHROPIC_API_KEY') else '未設定'})"
    )
    return None


def generate_jra_script(
    sanctions: list[dict[str, Any]],
    news: list[dict[str, Any]],
) -> str:
    """
    JRA向けナレーション原稿を生成する（60秒以内・約350文字）。

    構成:
      1. オープニング（3秒）: 「本日のJRA情報をお届けします！」
      2. 制裁情報（20秒）: あれば詳細、なければ「本日制裁情報はありませんでした」
      3. 当日の出来事（30秒）: ニュースを2〜3件テンポよく紹介
      4. クロージング（5秒）: 「以上、本日のJRA情報でした！チャンネル登録よろしく！」
    """
    today = datetime.now(JST).strftime("%m月%d日")
    logger.info("[INFO] JRA向けナレーション原稿の生成を開始")

    prompt = f"""以下の{today}のJRA情報をもとに、YouTube Shorts用のナレーション原稿を作成してください。

【制裁情報】
{_format_sanctions(sanctions)}

【本日の出来事（開催競馬場の今日の出来事）】
{_format_news(news)}

【原稿構成（必ず守ること）】
1. オープニング（3秒）: 「本日のJRA情報をお届けします！」から始める
2. 制裁情報（20秒分）:
   - 情報がある場合: 競馬場名・レース番号・騎手名・馬名・制裁内容を必ず読み上げること
     例）「○○競馬場○Rで○○騎手が騎乗した○○に対し、○○の制裁が科されました」
   - 情報がない場合: 「本日、制裁情報はありませんでした」と読む
3. 当日の出来事（30秒分）: 出来事の中から競走除外・競走中止・記録達成など話題性の高い内容を2〜3件紹介
4. クロージング（5秒）: 「以上、本日のJRA情報でした！チャンネル登録よろしく！」で締める

合計350文字程度で、話し言葉の原稿のみ出力してください（余計な説明文は不要）。"""

    result = _call_ai(prompt)
    if result:
        logger.info("[INFO] AI原稿生成成功")
        return result

    logger.warning("[WARNING] AI生成失敗 → フォールバック原稿を使用")
    return _fallback_jra_script(today, sanctions, news)


def generate_nar_script(
    sanctions: list[dict[str, Any]],
    news: list[dict[str, Any]],
) -> str:
    """
    地方競馬（NAR）向けナレーション原稿を生成する（60秒以内・約350文字）。
    """
    today = datetime.now(JST).strftime("%m月%d日")
    logger.info("[INFO] NAR向けナレーション原稿の生成を開始")

    prompt = f"""以下の{today}の地方競馬（NAR）情報をもとに、YouTube Shorts用のナレーション原稿を作成してください。

【制裁情報】
{_format_sanctions(sanctions)}

【本日のニュース・トピック】
{_format_news(news)}

【原稿構成（必ず守ること）】
1. オープニング（3秒）: 「本日の地方競馬情報をお届けします！」から始める
2. 制裁情報（20秒分）: 情報があれば詳しく、なければ「本日、制裁情報はありませんでした」と自然につなげる
3. 当日の出来事（30秒分）: トピックを2〜3件テンポよく紹介（情報がない場合は地方競馬の近況や今日の開催競馬場を紹介）
4. クロージング（5秒）: 「以上、本日の地方競馬情報でした！チャンネル登録よろしく！」で締める

合計350文字程度で、話し言葉の原稿のみ出力してください（余計な説明文は不要）。"""

    result = _call_ai(prompt)
    if result:
        return result

    return _fallback_nar_script(today, sanctions, news)


def _fallback_jra_script(
    today: str,
    sanctions: list[dict[str, Any]],
    news: list[dict[str, Any]],
) -> str:
    """
    AI生成失敗時のJRAフォールバック原稿。
    全制裁を競馬場ごとにまとめ、ニュースからキーワードで要点を抽出する。
    """
    from collections import defaultdict

    parts = ["本日のJRA情報をお届けします！"]

    # ── 制裁情報（全件・競馬場ごとにまとめ）──
    if sanctions:
        by_venue: dict[str, list] = defaultdict(list)
        for s in sanctions:
            by_venue[s.get("venue", "不明")].append(s)

        sanction_lines = ["本日の制裁情報です。"]
        for venue, items in list(by_venue.items())[:3]:
            first = items[0]
            jockey = first.get("jockey", "")
            horse = first.get("horse", "")
            race = first.get("race", "")
            content = first.get("content", "")[:20]
            horse_str = f"（{horse}）" if horse else ""
            if len(items) == 1:
                sanction_lines.append(
                    f"{venue}{race}で{jockey}騎手{horse_str}に{content}の制裁。"
                )
            else:
                sanction_lines.append(
                    f"{venue}では{jockey}騎手{horse_str}など{len(items)}件の制裁。"
                )
        parts.append("　".join(sanction_lines))
    else:
        parts.append("本日、制裁情報はありませんでした。")

    # ── 今日の出来事（ニュースから要点抽出）──
    if news:
        summary = news[0].get("summary", "")
        highlights = []

        # 記録達成・GⅠ結果を優先抽出
        for kw in ["勝達成", "通算1,", "通算1000", "史上", "GⅠ", "賞（G"]:
            idx = summary.find(kw)
            if idx >= 0:
                start = max(0, idx - 15)
                end = min(len(summary), idx + 60)
                snippet = summary[start:end].replace("　", "").strip()
                # 重複除去
                if not any(snippet[:15] in h for h in highlights):
                    highlights.append(snippet)

        if highlights:
            parts.append("続いて本日の出来事です。" + "。".join(h.rstrip("。") for h in highlights[:3]) + "。")
        elif summary:
            parts.append(f"続いて本日の出来事です。{summary[:120]}")
        else:
            parts.append("本日も競馬場でレースが行われました。")
    else:
        parts.append("本日も競馬場でレースが行われました。")

    parts.append("以上、本日のJRA情報でした！チャンネル登録よろしく！")
    return "　".join(parts)


def _fallback_nar_script(
    today: str,
    sanctions: list[dict[str, Any]],
    news: list[dict[str, Any]],
) -> str:
    """AI生成失敗時のNARフォールバック原稿"""
    from collections import defaultdict

    parts = ["本日の地方競馬情報をお届けします！"]

    if sanctions:
        by_venue: dict[str, list] = defaultdict(list)
        for s in sanctions:
            by_venue[s.get("venue", "不明")].append(s)
        sanction_lines = ["本日の制裁情報です。"]
        for venue, items in list(by_venue.items())[:3]:
            first = items[0]
            jockey = first.get("jockey", "")
            content = first.get("content", "")[:20]
            if len(items) == 1:
                sanction_lines.append(f"{venue}で{jockey}騎手に{content}の制裁。")
            else:
                sanction_lines.append(f"{venue}では{jockey}騎手など{len(items)}件の制裁。")
        parts.append("　".join(sanction_lines))
    else:
        parts.append("本日、制裁情報はありませんでした。")

    if news:
        summary = news[0].get("summary", "")
        parts.append(f"続いて本日のトピックです。{summary[:120]}" if summary else "本日も全国の競馬場でレースが行われました。")
    else:
        parts.append("本日も全国の競馬場でレースが行われました。")

    parts.append("以上、本日の地方競馬情報でした！チャンネル登録よろしく！")
    return "　".join(parts)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    print("=== AI原稿生成 テスト ===")
    gemini_key = os.getenv("GEMINI_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    print(f"Gemini API Key: {'設定済み' if gemini_key else '未設定'}")
    print(f"Anthropic API Key: {'設定済み' if anthropic_key else '未設定'}")

    dummy_sanctions = [
        {"date": "2024年4月12日", "jockey": "田中騎手", "content": "騎乗停止3日間", "reason": "落馬妨害"}
    ]
    dummy_news = [
        {"title": "桜花賞レース結果", "date": "2024年4月12日", "summary": "1番人気が優勝"},
        {"title": "新馬戦デビュー情報", "date": "2024年4月12日", "summary": "注目の新馬が出走"},
    ]

    print("\n--- JRA原稿 ---")
    jra_script = generate_jra_script(dummy_sanctions, dummy_news)
    print(jra_script)

    print("\n--- NAR原稿 ---")
    nar_script = generate_nar_script([], dummy_news[:1])
    print(nar_script)

    print("\n✅ ステップ5完了: script_generator.py テスト成功")
