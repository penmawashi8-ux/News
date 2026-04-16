"""
JRA YouTube Shorts 自動投稿 エントリーポイント

実行フロー:
  1. JRA開催日判定 → 開催なしなら終了
  2. JRAスクレイピング（制裁情報 + ニュース）
  3. AI原稿生成
  4. VOICEVOX音声生成
  5. 動画生成
  6. YouTube投稿（--dry-runでなければ）
  7. 一時ファイル削除（ドライランモードでは音声・動画ファイルを保持）
  8. 結果サマリーをログ出力

使用方法:
  python main_jra.py                      # 通常実行（YouTube投稿あり）
  python main_jra.py --dry-run            # ドライラン（投稿なし・動画をoutput/に保存）
  python main_jra.py --dry-run --force    # 開催日問わずドライラン実行
  python main_jra.py --scrape-only        # スクレイピング確認（テキスト出力のみ）
  python main_jra.py --scrape-only --force
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import pytz
from dotenv import load_dotenv

# プロジェクトルートをPythonパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from src.ai.script_generator import (
    generate_jra_news_script,
    generate_jra_sanctions_script,
)
from src.scraper.jra_scraper import get_news, get_sanctions
from src.tts.voicevox_tts import register_user_dict, text_to_speech_segmented
from src.uploader.youtube_uploader import (
    JRA_NEWS_TAGS,
    JRA_SANCTIONS_TAGS,
    build_jra_news_description,
    build_jra_news_title,
    build_jra_sanctions_description,
    build_jra_sanctions_title,
    upload_video,
)
from src.utils.calendar import is_jra_race_day
from src.utils.logger import get_logger
from src.video.video_builder import build_video

# 環境変数を読み込む（.envファイルがあれば）
load_dotenv()

logger = get_logger(__name__)

JST = pytz.timezone("Asia/Tokyo")

# 出力ディレクトリ
OUTPUT_DIR = Path("output")


def parse_args() -> argparse.Namespace:
    """コマンドライン引数をパースする"""
    parser = argparse.ArgumentParser(
        description="JRA競馬情報をYouTube Shortsに自動投稿するスクリプト"
    )
    parser.add_argument(
        "--scrape-only",
        action="store_true",
        help="スクレイピングとAI原稿生成のみ。音声・動画・YouTube投稿をスキップしてテキスト出力する",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="YouTubeへの投稿をスキップして動画ファイルのみ生成する",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="JRA開催日判定をスキップして強制実行する",
    )
    return parser.parse_args()


def write_step_summary(content: str) -> None:
    """
    GitHub Actions の Step Summary にマークダウンを書き出す。
    ローカル実行時は何もしない。

    Args:
        content: マークダウン形式のテキスト
    """
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(content + "\n")


def _run_scrape_only(date_str: str, date_filename: str) -> int:
    """
    スクレイプ確認モード。
    スクレイピング + AI原稿生成のみ実行し、結果をテキストファイルと
    GitHub Step Summary に出力する。音声・動画・YouTube投稿は行わない。

    Returns:
        終了コード（0: 成功、1: エラー）
    """
    logger.info("[INFO] ===== スクレイプ確認モード =====")

    try:
        logger.info("[INFO] Step 2: JRAスクレイピング開始")
        sanctions = get_sanctions()
        news = get_news()
        logger.info(f"[INFO] 制裁情報: {len(sanctions)}件, ニュース: {len(news)}件")

        logger.info("[INFO] Step 3: AI原稿生成開始")
        sanctions_script = generate_jra_sanctions_script(sanctions) if sanctions else "（制裁情報なし）"
        news_script = generate_jra_news_script(news) if news else "（ニュースなし）"
        logger.info(f"[INFO] 制裁原稿生成完了（{len(sanctions_script)}文字）")
        logger.info(f"[INFO] ニュース原稿生成完了（{len(news_script)}文字）")

        # ---- テキスト整形 ----
        lines = []
        lines.append(f"=== JRA スクレイプ結果 ({date_str}) ===\n")

        lines.append("--- 制裁情報 ---")
        if sanctions:
            for s in sanctions:
                horse = s.get("horse", "")
                jockey_horse = s.get("jockey", "（不明）")
                if horse:
                    jockey_horse += f"（{horse}）"
                lines.append(
                    f"  [{s.get('venue', '')} {s.get('race', '')}]"
                    f" 騎手: {jockey_horse}"
                    f" / 制裁: {s.get('content', '')}"
                )
                if s.get("reason"):
                    lines.append(f"    短評: {s['reason']}")
        else:
            lines.append("  （制裁情報なし）")

        lines.append("")
        lines.append("--- ニュース（今日の出来事）---")
        if news:
            for n in news:
                lines.append(f"  [{n.get('date', '')}] {n.get('title', '')}")
                if n.get("summary"):
                    lines.append(f"    {n['summary']}")
        else:
            lines.append("  （ニュースなし）")

        lines.append("")
        lines.append("--- AI生成原稿（制裁情報動画）---")
        lines.append(sanctions_script)
        lines.append("")
        lines.append("--- AI生成原稿（ニュース動画）---")
        lines.append(news_script)

        text_output = "\n".join(lines)

        # ---- ファイル出力 ----
        OUTPUT_DIR.mkdir(exist_ok=True)
        out_path = OUTPUT_DIR / f"jra_scrape_{date_filename}.txt"
        out_path.write_text(text_output, encoding="utf-8")
        logger.info(f"[INFO] テキスト出力: {out_path.resolve()}")

        # ---- コンソール出力 ----
        print("\n" + text_output)

        # ---- GitHub Step Summary ----
        md_lines = [
            f"## JRA スクレイプ確認結果 ({date_str})\n",
            "### 制裁情報",
        ]
        if sanctions:
            for s in sanctions:
                venue_race = f"{s.get('venue', '')} {s.get('race', '')}".strip()
                horse = s.get("horse", "")
                jockey_horse = s.get("jockey", "（不明）")
                if horse:
                    jockey_horse += f"（{horse}）"
                md_lines.append(
                    f"- **{venue_race}** / 騎手: {jockey_horse}"
                    f" / 制裁: {s.get('content', '')}"
                )
                if s.get("reason"):
                    md_lines.append(f"  - 短評: {s['reason']}")
        else:
            md_lines.append("- （制裁情報なし）")

        md_lines += ["", "### ニュース（今日の出来事）"]
        if news:
            for n in news:
                md_lines.append(f"- **{n.get('title', '')}** ({n.get('date', '')})")
                if n.get("summary"):
                    md_lines.append(f"  {n['summary']}")
        else:
            md_lines.append("- （ニュースなし）")

        md_lines += [
            "",
            "### AI生成原稿（制裁情報動画）",
            "```",
            sanctions_script,
            "```",
            "",
            "### AI生成原稿（ニュース動画）",
            "```",
            news_script,
            "```",
        ]
        write_step_summary("\n".join(md_lines))

        logger.info("[INFO] スクレイプ確認モード 完了")
        return 0

    except Exception as e:
        logger.error(f"[ERROR] スクレイプ確認中にエラー: {e}", exc_info=True)
        write_step_summary(f"## JRA スクレイプ確認 失敗\n\n```\n{e}\n```\n")
        return 1


def cleanup_temp_files(*file_paths: str) -> None:
    """
    一時ファイルを削除する。

    Args:
        *file_paths: 削除するファイルパスのリスト
    """
    for path in file_paths:
        if path and Path(path).exists():
            try:
                Path(path).unlink()
                logger.info(f"[INFO] 一時ファイル削除: {path}")
            except Exception as e:
                logger.warning(f"[WARNING] 一時ファイルの削除に失敗: {path} - {e}")


def main() -> int:
    """
    メイン実行関数。

    Returns:
        終了コード（0: 成功、1: エラー）
    """
    args = parse_args()
    now = datetime.now(JST)
    date_str = now.strftime("%m/%d")
    date_filename = now.strftime("%Y%m%d")

    logger.info("=" * 60)
    logger.info("[INFO] JRA YouTube Shorts 自動投稿 開始")
    logger.info(f"[INFO] 実行日時: {now.strftime('%Y-%m-%d %H:%M:%S JST')}")
    logger.info(f"[INFO] モード: {'スクレイプ確認' if args.scrape_only else 'ドライラン' if args.dry_run else '本番'}")
    logger.info("=" * 60)

    # ドライランは環境変数でも指定可能
    dry_run = args.dry_run or os.getenv("DRY_RUN", "false").lower() == "true"

    # ========== Step 0: VOICEVOXユーザー辞書登録 ==========
    register_user_dict()

    # ========== Step 1: JRA開催日判定 ==========
    if not args.force:
        logger.info("[INFO] Step 1: JRA開催日判定")
        if not is_jra_race_day():
            logger.info("[INFO] 本日はJRA開催なし。処理を終了します。")
            return 0
        logger.info("[INFO] 本日はJRA開催日です。処理を続行します。")
    else:
        logger.info("[INFO] Step 1: --force オプションにより開催日判定をスキップ")

    # ========== スクレイプ確認モード ==========
    if args.scrape_only:
        return _run_scrape_only(date_str, date_filename)

    # 生成ファイルを追跡（後処理用）
    temp_files: list[str] = []

    try:
        # ========== Step 2: JRAスクレイピング ==========
        logger.info("[INFO] Step 2: JRAスクレイピング開始")
        sanctions = get_sanctions()
        news = get_news()
        logger.info(f"[INFO] 制裁情報: {len(sanctions)}件, ニュース: {len(news)}件")

        OUTPUT_DIR.mkdir(exist_ok=True)

        # ========== Step 3 〜 6: 制裁情報動画 ==========
        sanctions_video_id = None
        sanctions_video_path = None
        if sanctions:
            logger.info("[INFO] Step 3a: 制裁情報AI原稿生成開始")
            sanctions_script = generate_jra_sanctions_script(sanctions)
            logger.info(f"[INFO] 制裁原稿生成完了（{len(sanctions_script)}文字）:\n{sanctions_script[:80]}...")

            logger.info("[INFO] Step 4a: 制裁情報音声生成開始")
            sanctions_audio_path = str(OUTPUT_DIR / f"jra_sanctions_audio_{date_filename}.wav")
            sanctions_audio_path, sanctions_seg_durations = text_to_speech_segmented(
                sanctions_script, sanctions_audio_path
            )
            temp_files.append(sanctions_audio_path)
            logger.info(f"[INFO] 制裁音声ファイル生成完了: {sanctions_audio_path}")

            logger.info("[INFO] Step 5a: 制裁情報動画生成開始")
            sanctions_video_path = str(OUTPUT_DIR / f"jra_sanctions_{date_filename}.mp4")
            sanctions_video_path = build_video(
                audio_path=sanctions_audio_path,
                script_text=sanctions_script,
                output_path=sanctions_video_path,
                theme="jra",
                segment_durations=sanctions_seg_durations,
            )
            temp_files.append(sanctions_video_path)
            logger.info(f"[INFO] 制裁動画ファイル生成完了: {sanctions_video_path}")

            if not dry_run:
                logger.info("[INFO] Step 6a: 制裁情報動画 YouTube投稿開始")
                sanctions_video_id = upload_video(
                    video_path=sanctions_video_path,
                    title=build_jra_sanctions_title(date_str),
                    description=build_jra_sanctions_description(sanctions_script),
                    tags=JRA_SANCTIONS_TAGS,
                )
                logger.info(f"[INFO] 制裁動画 投稿完了: https://www.youtube.com/watch?v={sanctions_video_id}")
        else:
            logger.info("[INFO] 制裁情報なし - 制裁情報動画をスキップ")

        # ========== Step 3 〜 6: ニュース動画 ==========
        news_video_id = None
        news_video_path = None
        if news:
            logger.info("[INFO] Step 3b: ニュースAI原稿生成開始")
            news_script = generate_jra_news_script(news)
            logger.info(f"[INFO] ニュース原稿生成完了（{len(news_script)}文字）:\n{news_script[:80]}...")

            logger.info("[INFO] Step 4b: ニュース音声生成開始")
            news_audio_path = str(OUTPUT_DIR / f"jra_news_audio_{date_filename}.wav")
            news_audio_path, news_seg_durations = text_to_speech_segmented(
                news_script, news_audio_path
            )
            temp_files.append(news_audio_path)
            logger.info(f"[INFO] ニュース音声ファイル生成完了: {news_audio_path}")

            logger.info("[INFO] Step 5b: ニュース動画生成開始")
            news_video_path = str(OUTPUT_DIR / f"jra_news_{date_filename}.mp4")
            news_video_path = build_video(
                audio_path=news_audio_path,
                script_text=news_script,
                output_path=news_video_path,
                theme="jra",
                segment_durations=news_seg_durations,
            )
            temp_files.append(news_video_path)
            logger.info(f"[INFO] ニュース動画ファイル生成完了: {news_video_path}")

            if not dry_run:
                logger.info("[INFO] Step 6b: ニュース動画 YouTube投稿開始")
                news_video_id = upload_video(
                    video_path=news_video_path,
                    title=build_jra_news_title(date_str),
                    description=build_jra_news_description(news_script),
                    tags=JRA_NEWS_TAGS,
                )
                logger.info(f"[INFO] ニュース動画 投稿完了: https://www.youtube.com/watch?v={news_video_id}")
        else:
            logger.info("[INFO] ニュース情報なし - ニュース動画をスキップ")

        # ========== Step 7: ファイル後処理 ==========
        logger.info("[INFO] Step 7: ファイル後処理")
        if dry_run:
            logger.info("[INFO] ドライランモード: 生成ファイルを保持します")
            for f in temp_files:
                if f and Path(f).exists():
                    logger.info(f"[INFO] 保持: {Path(f).resolve()}")
            logger.info("[INFO] 確認後に不要であれば output/ ディレクトリを手動削除してください")
        else:
            # 本番モード: 投稿済みの一時ファイルを削除
            cleanup_temp_files(*temp_files)

        # ========== Step 8: 結果サマリー ==========
        uploaded_count = sum(1 for v in [sanctions_video_id, news_video_id] if v)
        logger.info("=" * 60)
        logger.info("[INFO] ===== 実行サマリー =====")
        logger.info(f"[INFO] 日付: {date_str}")
        logger.info(f"[INFO] 制裁情報取得: {len(sanctions)}件")
        logger.info(f"[INFO] ニュース取得: {len(news)}件")
        if sanctions_video_id:
            logger.info(f"[INFO] 制裁情報動画URL: https://www.youtube.com/watch?v={sanctions_video_id}")
        if news_video_id:
            logger.info(f"[INFO] ニュース動画URL: https://www.youtube.com/watch?v={news_video_id}")
        if dry_run:
            for f in temp_files:
                if f and Path(f).exists() and f.endswith(".mp4"):
                    logger.info(f"[INFO] 動画確認パス: {Path(f).resolve()}")
            logger.info("[INFO] ★ ドライランのため YouTube には投稿していません ★")
        logger.info("[INFO] JRA YouTube Shorts 自動投稿 完了")
        logger.info("=" * 60)

        # GitHub Actions Step Summary に結果を書き出す
        sanctions_videos_generated = sanctions_video_path is not None
        news_videos_generated = news_video_path is not None
        if not dry_run:
            summary_rows = (
                f"| 日付 | {date_str} |\n"
                f"| 制裁情報 | {len(sanctions)}件 |\n"
                f"| ニュース | {len(news)}件 |\n"
            )
            if sanctions_video_id:
                summary_rows += f"| 制裁動画URL | https://www.youtube.com/watch?v={sanctions_video_id} |\n"
            if news_video_id:
                summary_rows += f"| ニュース動画URL | https://www.youtube.com/watch?v={news_video_id} |\n"
            write_step_summary(
                f"## ✅ JRA Shorts 投稿完了（{uploaded_count}本）\n\n"
                f"| 項目 | 値 |\n|------|----|\n"
                + summary_rows
            )
        else:
            write_step_summary(
                f"## ✅ JRA Shorts 動画生成完了（ドライラン）\n\n"
                f"| 項目 | 値 |\n|------|----|\n"
                f"| 日付 | {date_str} |\n"
                f"| 制裁情報 | {len(sanctions)}件 |\n"
                f"| ニュース | {len(news)}件 |\n"
                f"| 生成動画数 | {sanctions_videos_generated + news_videos_generated}本 |\n\n"
                f"生成した動画は **Artifacts** からダウンロードして確認してください。\n"
            )
        return 0

    except Exception as e:
        logger.error(f"[ERROR] 実行中にエラーが発生しました: {e}", exc_info=True)
        write_step_summary(
            f"## ❌ JRA Shorts 生成失敗\n\n"
            f"```\n{e}\n```\n\n"
            f"詳細はログを確認してください。\n"
        )
        # エラー時は音声ファイルのみ削除（動画は原因調査のために残す）
        if not dry_run:
            audio_files = [f for f in temp_files if f.endswith(".wav")]
            cleanup_temp_files(*audio_files)
        return 1


if __name__ == "__main__":
    sys.exit(main())
