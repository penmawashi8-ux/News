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

from src.ai.script_generator import generate_jra_script
from src.scraper.jra_scraper import get_news, get_sanctions
from src.tts.voicevox_tts import text_to_speech
from src.uploader.youtube_uploader import (
    JRA_TAGS,
    build_jra_description,
    build_jra_title,
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
    logger.info(f"[INFO] ドライランモード: {args.dry_run}")
    logger.info("=" * 60)

    # ドライランは環境変数でも指定可能
    dry_run = args.dry_run or os.getenv("DRY_RUN", "false").lower() == "true"

    # ========== Step 1: JRA開催日判定 ==========
    if not args.force:
        logger.info("[INFO] Step 1: JRA開催日判定")
        if not is_jra_race_day():
            logger.info("[INFO] 本日はJRA開催なし。処理を終了します。")
            return 0
        logger.info("[INFO] 本日はJRA開催日です。処理を続行します。")
    else:
        logger.info("[INFO] Step 1: --force オプションにより開催日判定をスキップ")

    audio_path = None
    video_path = None

    try:
        # ========== Step 2: JRAスクレイピング ==========
        logger.info("[INFO] Step 2: JRAスクレイピング開始")
        sanctions = get_sanctions()
        news = get_news()
        logger.info(f"[INFO] 制裁情報: {len(sanctions)}件, ニュース: {len(news)}件")

        # ========== Step 3: AI原稿生成 ==========
        logger.info("[INFO] Step 3: AI原稿生成開始")
        script = generate_jra_script(sanctions, news)
        logger.info(f"[INFO] 原稿生成完了（{len(script)}文字）:\n{script[:100]}...")

        # ========== Step 4: VOICEVOX音声生成 ==========
        logger.info("[INFO] Step 4: 音声生成開始")
        OUTPUT_DIR.mkdir(exist_ok=True)
        audio_path = str(OUTPUT_DIR / f"jra_audio_{date_filename}.wav")
        audio_path = text_to_speech(script, audio_path)
        logger.info(f"[INFO] 音声ファイル生成完了: {audio_path}")

        # ========== Step 5: 動画生成 ==========
        logger.info("[INFO] Step 5: 動画生成開始")
        video_path = str(OUTPUT_DIR / f"jra_{date_filename}.mp4")
        video_path = build_video(
            audio_path=audio_path,
            script_text=script,
            output_path=video_path,
            theme="jra",
        )
        logger.info(f"[INFO] 動画ファイル生成完了: {video_path}")

        # ========== Step 6: YouTube投稿 ==========
        if not dry_run:
            logger.info("[INFO] Step 6: YouTube投稿開始")
            title = build_jra_title(date_str)
            description = build_jra_description(script)
            video_id = upload_video(
                video_path=video_path,
                title=title,
                description=description,
                tags=JRA_TAGS,
            )
            youtube_url = f"https://www.youtube.com/watch?v={video_id}"
            logger.info(f"[INFO] YouTube投稿完了: {youtube_url}")
        else:
            logger.info("[INFO] Step 6: ドライランモード - YouTube投稿をスキップ")
            video_id = "DRY_RUN"

        # ========== Step 7: ファイル後処理 ==========
        logger.info("[INFO] Step 7: ファイル後処理")
        if dry_run:
            # ドライランモード: 音声・動画ファイルを両方 output/ に保持して確認できるようにする
            abs_video = Path(video_path).resolve()
            abs_audio = Path(audio_path).resolve()
            logger.info("[INFO] ドライランモード: 生成ファイルを保持します")
            logger.info(f"[INFO] 動画ファイル : {abs_video}")
            logger.info(f"[INFO] 音声ファイル : {abs_audio}")
            logger.info("[INFO] 確認後に不要であれば output/ ディレクトリを手動削除してください")
        else:
            # 本番モード: 投稿済みの一時ファイルを削除
            cleanup_temp_files(audio_path, video_path)

        # ========== Step 8: 結果サマリー ==========
        logger.info("=" * 60)
        logger.info("[INFO] ===== 実行サマリー =====")
        logger.info(f"[INFO] 日付: {date_str}")
        logger.info(f"[INFO] 制裁情報取得: {len(sanctions)}件")
        logger.info(f"[INFO] ニュース取得: {len(news)}件")
        logger.info(f"[INFO] 原稿文字数: {len(script)}文字")
        if not dry_run:
            logger.info(f"[INFO] YouTube動画ID: {video_id}")
            logger.info(f"[INFO] 動画URL: https://www.youtube.com/watch?v={video_id}")
        else:
            logger.info(f"[INFO] 動画確認パス: {Path(video_path).resolve()}")
            logger.info("[INFO] ★ ドライランのため YouTube には投稿していません ★")
        logger.info("[INFO] JRA YouTube Shorts 自動投稿 完了")
        logger.info("=" * 60)

        # GitHub Actions Step Summary に結果を書き出す
        if not dry_run:
            write_step_summary(
                f"## ✅ JRA Shorts 投稿完了\n\n"
                f"| 項目 | 値 |\n|------|----|\n"
                f"| 日付 | {date_str} |\n"
                f"| 制裁情報 | {len(sanctions)}件 |\n"
                f"| ニュース | {len(news)}件 |\n"
                f"| 原稿文字数 | {len(script)}文字 |\n"
                f"| 動画URL | https://www.youtube.com/watch?v={video_id} |\n"
            )
        else:
            write_step_summary(
                f"## ✅ JRA Shorts 動画生成完了（ドライラン）\n\n"
                f"| 項目 | 値 |\n|------|----|\n"
                f"| 日付 | {date_str} |\n"
                f"| 制裁情報 | {len(sanctions)}件 |\n"
                f"| ニュース | {len(news)}件 |\n"
                f"| 原稿文字数 | {len(script)}文字 |\n\n"
                f"生成した動画は **Artifacts** からダウンロードして確認してください。\n"
            )
        return 0

    except Exception as e:
        logger.error(f"[ERROR] 実行中にエラーが発生しました: {e}", exc_info=True)
        # GitHub Actions Step Summary にエラーを書き出す
        write_step_summary(
            f"## ❌ JRA Shorts 生成失敗\n\n"
            f"```\n{e}\n```\n\n"
            f"詳細はログを確認してください。\n"
        )
        # エラー時は音声のみ削除（動画は中途半端でも残して原因調査に使える）
        if not dry_run:
            cleanup_temp_files(audio_path)
        return 1


if __name__ == "__main__":
    sys.exit(main())
