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
    """制裁情報をプロンプト用テキストに変換する"""
    if not sanctions:
        return "（制裁情報なし）"
    lines = []
    for s in sanctions[:5]:
        parts = []
        if s.get("venue") and s.get("race"):
            parts.append(f"場所:{s['venue']} {s['race']}")
        elif s.get("race"):
            parts.append(f"レース:{s['race']}")
        if s.get("date"):
            parts.append(f"日付:{s['date']}")
        if s.get("jockey"):
            parts.append(f"対象:{s['jockey']}")
        if s.get("content"):
            parts.append(f"内容:{s['content'][:80]}")
        if s.get("reason"):
            parts.append(f"理由:{s['reason'][:80]}")
        lines.append(" / ".join(parts))
    return "\n".join(lines)


def _format_news(news: list[dict[str, Any]]) -> str:
    """ニュースをプロンプト用テキストに変換する"""
    if not news:
        return "（ニュースなし）"
    lines = []
    for n in news[:3]:
        title = n.get("title", "")
        date = n.get("date", "")
        summary = n.get("summary", "")
        line = f"・{title}"
        if date:
            line += f"（{date}）"
        if summary:
            line += f" - {summary[:60]}"
        lines.append(line)
    return "\n".join(lines)


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

    logger.warning("[WARNING] AI API キーが未設定のためフォールバックテンプレートを使用します")
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

【本日のニュース・出来事】
{_format_news(news)}

【原稿構成（必ず守ること）】
1. オープニング（3秒）: 「本日のJRA情報をお届けします！」から始める
2. 制裁情報（20秒分）: 情報があれば詳しく、なければ「本日、制裁情報はありませんでした」と自然につなげる
3. 当日の出来事（30秒分）: ニュースを2〜3件テンポよく紹介（情報がない場合はJRAの近況や一般的な競馬情報で埋める）
4. クロージング（5秒）: 「以上、本日のJRA情報でした！チャンネル登録よろしく！」で締める

合計350文字程度で、話し言葉の原稿のみ出力してください（余計な説明文は不要）。"""

    result = _call_ai(prompt)
    if result:
        return result

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
    """AI生成失敗時のJRAフォールバック原稿"""
    sanction_part = (
        f"本日の制裁情報です。{sanctions[0]['jockey']}騎手に{sanctions[0]['content'][:30]}の制裁が科されました。"
        if sanctions else "本日、制裁情報はありませんでした。"
    )
    news_part = (
        "　".join(f"{n['title'][:30]}。" for n in news[:2])
        if news else "本日も競馬場でレースが行われました。"
    )
    return (
        f"本日のJRA情報をお届けします！"
        f"　{sanction_part}"
        f"　続いて本日のニュースです。{news_part}"
        f"　以上、本日のJRA情報でした！チャンネル登録よろしく！"
    )


def _fallback_nar_script(
    today: str,
    sanctions: list[dict[str, Any]],
    news: list[dict[str, Any]],
) -> str:
    """AI生成失敗時のNARフォールバック原稿"""
    sanction_part = (
        f"本日の制裁情報です。{sanctions[0]['content'][:40]}。"
        if sanctions else "本日、制裁情報はありませんでした。"
    )
    news_part = (
        "　".join(f"{n['title'][:30]}。" for n in news[:2])
        if news else "本日も全国の競馬場でレースが行われました。"
    )
    return (
        f"本日の地方競馬情報をお届けします！"
        f"　{sanction_part}"
        f"　続いて本日のトピックです。{news_part}"
        f"　以上、本日の地方競馬情報でした！チャンネル登録よろしく！"
    )


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
