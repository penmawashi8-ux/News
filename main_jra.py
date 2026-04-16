"""
JRA YouTube Shorts 自動投稿 エントリーポイント

実行フロー:
  1. JRA開催日判定 → 開催なしなら終了
  2. JRAスクレイピング（制裁情報 + ニュース）
  3. 競馬場ごとにグループ化
  4. AI原稿生成（競馬場単位）
  5. VOICEVOX音声生成（セグメント別・タイミング情報付き）
  6. 動画生成
  7. YouTube投稿（--dry-runでなければ）
  8. 一時ファイル削除
  9. 結果サマリー出力

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
    upload_thumbnail,
    upload_video,
)
from src.utils.calendar import is_jra_race_day
from src.utils.logger import get_logger
from src.video.video_builder import build_video, generate_thumbnail_image

# 環境変数を読み込む（.envファイルがあれば）
load_dotenv()

logger = get_logger(__name__)

JST = pytz.timezone("Asia/Tokyo")

# 出力ディレクトリ
OUTPUT_DIR = Path("output")

# 競馬場名リスト（venue抽出に使用）
JRA_VENUE_NAMES = ["札幌", "函館", "福島", "新潟", "中山", "東京", "中京", "京都", "阪神", "小倉"]


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
    """
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(content + "\n")


def _get_event_venue(event: dict) -> str:
    """イベントデータから競馬場名を抽出する。section → race → title の順で探す。"""
    for field in ("section", "race", "title"):
        text = event.get(field, "")
        for venue in JRA_VENUE_NAMES:
            if venue in text:
                return venue
    return "その他"


def _group_sanctions_by_venue(sanctions: list[dict]) -> dict[str, list[dict]]:
    """制裁情報を競馬場ごとにグループ化する（出現順を保持）。"""
    result: dict[str, list[dict]] = {}
    for s in sanctions:
        venue = s.get("venue") or "その他"
        result.setdefault(venue, []).append(s)
    return result


def _group_news_by_venue(news: list[dict]) -> dict[str, list[dict]]:
    """
    ニュース記事のイベントを競馬場ごとにグループ化する。
    Returns: {venue: [合成ニュース記事]}  ← generate_jra_news_script に渡せる形式
    """
    events_by_venue: dict[str, list[dict]] = {}
    for article in news:
        for event in article.get("events", []):
            venue = _get_event_venue(event)
            events_by_venue.setdefault(venue, []).append(event)

    return {
        venue: [{"title": f"{venue} 今日の出来事", "date": "", "summary": "", "events": events}]
        for venue, events in events_by_venue.items()
    }


def _safe_filename_venue(venue: str) -> str:
    """競馬場名をファイル名に使える形に変換する。"""
    return venue.replace("/", "_").replace("\\", "_").replace(" ", "_")


def _run_scrape_only(date_str: str, date_filename: str) -> int:
    """
    スクレイプ確認モード。
    スクレイピング + AI原稿生成のみ実行し、結果をテキストファイルと
    GitHub Step Summary に出力する。音声・動画・YouTube投稿は行わない。
    """
    logger.info("[INFO] ===== スクレイプ確認モード =====")

    try:
        logger.info("[INFO] Step 2: JRAスクレイピング開始")
        sanctions = get_sanctions()
        news = get_news()
        logger.info(f"[INFO] 制裁情報: {len(sanctions)}件, ニュース: {len(news)}件")

        sanctions_by_venue = _group_sanctions_by_venue(sanctions)
        news_by_venue = _group_news_by_venue(news)

        logger.info("[INFO] Step 3: AI原稿生成開始")

        lines = [f"=== JRA スクレイプ結果 ({date_str}) ===\n"]

        # 制裁情報
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

        # 制裁原稿（競馬場ごと）
        lines.append("")
        lines.append("--- AI生成原稿（制裁情報動画）---")
        if sanctions_by_venue:
            for venue, venue_sanctions in sanctions_by_venue.items():
                script = generate_jra_sanctions_script(venue_sanctions)
                lines.append(f"\n【{venue}】{script}")
        else:
            lines.append("（制裁情報なし）")

        # ニュース原稿（競馬場ごと）
        lines.append("")
        lines.append("--- AI生成原稿（ニュース動画）---")
        if news_by_venue:
            for venue, venue_news in news_by_venue.items():
                script = generate_jra_news_script(venue_news)
                lines.append(f"\n【{venue}】{script}")
        else:
            lines.append("（ニュースなし）")

        text_output = "\n".join(lines)

        OUTPUT_DIR.mkdir(exist_ok=True)
        out_path = OUTPUT_DIR / f"jra_scrape_{date_filename}.txt"
        out_path.write_text(text_output, encoding="utf-8")
        logger.info(f"[INFO] テキスト出力: {out_path.resolve()}")
        print("\n" + text_output)

        # GitHub Step Summary
        md_lines = [f"## JRA スクレイプ確認結果 ({date_str})\n", "### 制裁情報"]
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
        else:
            md_lines.append("- （制裁情報なし）")

        md_lines += ["", "### ニュース（今日の出来事）"]
        if news:
            for n in news:
                md_lines.append(f"- **{n.get('title', '')}** ({n.get('date', '')})")
        else:
            md_lines.append("- （ニュースなし）")

        write_step_summary("\n".join(md_lines))
        logger.info("[INFO] スクレイプ確認モード 完了")
        return 0

    except Exception as e:
        logger.error(f"[ERROR] スクレイプ確認中にエラー: {e}", exc_info=True)
        write_step_summary(f"## JRA スクレイプ確認 失敗\n\n```\n{e}\n```\n")
        return 1


def cleanup_temp_files(*file_paths: str) -> None:
    """一時ファイルを削除する。"""
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

    temp_files: list[str] = []

    try:
        # ========== Step 2: JRAスクレイピング ==========
        logger.info("[INFO] Step 2: JRAスクレイピング開始")
        sanctions = get_sanctions()
        news = get_news()
        logger.info(f"[INFO] 制裁情報: {len(sanctions)}件, ニュース: {len(news)}件")

        OUTPUT_DIR.mkdir(exist_ok=True)

        # 競馬場ごとにグループ化
        sanctions_by_venue = _group_sanctions_by_venue(sanctions)
        news_by_venue = _group_news_by_venue(news)
        logger.info(
            f"[INFO] 競馬場別グループ: "
            f"制裁={list(sanctions_by_venue.keys())}, "
            f"ニュース={list(news_by_venue.keys())}"
        )

        # 生成した動画の記録
        sanctions_results: list[tuple[str, str, str | None]] = []  # (venue, path, video_id)
        news_results: list[tuple[str, str, str | None]] = []

        # ========== Step 3〜6: 制裁情報動画（競馬場ごと）==========
        if sanctions_by_venue:
            for venue, venue_sanctions in sanctions_by_venue.items():
                logger.info(f"[INFO] ===== 制裁情報動画: {venue} ({len(venue_sanctions)}件) =====")

                logger.info(f"[INFO] Step 3a: 制裁情報AI原稿生成 ({venue})")
                script = generate_jra_sanctions_script(venue_sanctions)
                logger.info(f"[INFO] 制裁原稿({venue}): {len(script)}文字")

                logger.info(f"[INFO] Step 4a: 制裁情報音声生成 ({venue})")
                venue_fn = _safe_filename_venue(venue)
                audio_path = str(OUTPUT_DIR / f"jra_sanctions_{venue_fn}_{date_filename}.wav")
                audio_path, seg_durs = text_to_speech_segmented(script, audio_path)
                temp_files.append(audio_path)

                logger.info(f"[INFO] Step 5a: 制裁情報動画生成 ({venue})")
                video_path = str(OUTPUT_DIR / f"jra_sanctions_{venue_fn}_{date_filename}.mp4")
                video_path = build_video(
                    audio_path=audio_path,
                    script_text=script,
                    output_path=video_path,
                    theme="jra",
                    segment_durations=seg_durs,
                    intro_date_str=date_str,
                    intro_venue=venue,
                    intro_video_type="制裁情報",
                )
                temp_files.append(video_path)

                # サムネイル画像生成
                thumb_path = str(OUTPUT_DIR / f"jra_sanctions_{venue_fn}_{date_filename}_thumb.png")
                generate_thumbnail_image(date_str, venue, "制裁情報", thumb_path)
                temp_files.append(thumb_path)

                video_id = None
                if not dry_run:
                    logger.info(f"[INFO] Step 6a: 制裁情報動画 YouTube投稿 ({venue})")
                    video_id = upload_video(
                        video_path=video_path,
                        title=build_jra_sanctions_title(date_str, venue),
                        description=build_jra_sanctions_description(script),
                        tags=JRA_SANCTIONS_TAGS,
                    )
                    logger.info(f"[INFO] 制裁動画({venue}) 投稿完了: https://www.youtube.com/watch?v={video_id}")
                    upload_thumbnail(video_id, thumb_path)

                sanctions_results.append((venue, video_path, video_id))
        else:
            logger.info("[INFO] 制裁情報なし - 制裁情報動画をスキップ")

        # ========== Step 3〜6: ニュース動画（競馬場ごと）==========
        if news_by_venue:
            for venue, venue_news in news_by_venue.items():
                event_count = sum(len(a.get("events", [])) for a in venue_news)
                logger.info(f"[INFO] ===== ニュース動画: {venue} ({event_count}件) =====")

                logger.info(f"[INFO] Step 3b: ニュースAI原稿生成 ({venue})")
                script = generate_jra_news_script(venue_news)
                logger.info(f"[INFO] ニュース原稿({venue}): {len(script)}文字")

                logger.info(f"[INFO] Step 4b: ニュース音声生成 ({venue})")
                venue_fn = _safe_filename_venue(venue)
                audio_path = str(OUTPUT_DIR / f"jra_news_{venue_fn}_{date_filename}.wav")
                audio_path, seg_durs = text_to_speech_segmented(script, audio_path)
                temp_files.append(audio_path)

                logger.info(f"[INFO] Step 5b: ニュース動画生成 ({venue})")
                video_path = str(OUTPUT_DIR / f"jra_news_{venue_fn}_{date_filename}.mp4")
                video_path = build_video(
                    audio_path=audio_path,
                    script_text=script,
                    output_path=video_path,
                    theme="jra",
                    segment_durations=seg_durs,
                    intro_date_str=date_str,
                    intro_venue=venue,
                    intro_video_type="今日の出来事",
                )
                temp_files.append(video_path)

                # サムネイル画像生成
                thumb_path = str(OUTPUT_DIR / f"jra_news_{venue_fn}_{date_filename}_thumb.png")
                generate_thumbnail_image(date_str, venue, "今日の出来事", thumb_path)
                temp_files.append(thumb_path)

                video_id = None
                if not dry_run:
                    logger.info(f"[INFO] Step 6b: ニュース動画 YouTube投稿 ({venue})")
                    video_id = upload_video(
                        video_path=video_path,
                        title=build_jra_news_title(date_str, venue),
                        description=build_jra_news_description(script),
                        tags=JRA_NEWS_TAGS,
                    )
                    logger.info(f"[INFO] ニュース動画({venue}) 投稿完了: https://www.youtube.com/watch?v={video_id}")
                    upload_thumbnail(video_id, thumb_path)

                news_results.append((venue, video_path, video_id))
        else:
            logger.info("[INFO] ニュース情報なし - ニュース動画をスキップ")

        # ========== Step 7: ファイル後処理 ==========
        logger.info("[INFO] Step 7: ファイル後処理")
        if dry_run:
            logger.info("[INFO] ドライランモード: 生成ファイルを保持します")
            for f in temp_files:
                if f and Path(f).exists():
                    logger.info(f"[INFO] 保持: {Path(f).resolve()}")
        else:
            cleanup_temp_files(*temp_files)

        # ========== Step 8: 結果サマリー ==========
        all_results = sanctions_results + news_results
        uploaded_count = sum(1 for _, _, vid in all_results if vid)
        video_count = len(all_results)

        logger.info("=" * 60)
        logger.info("[INFO] ===== 実行サマリー =====")
        logger.info(f"[INFO] 日付: {date_str}")
        logger.info(f"[INFO] 制裁情報取得: {len(sanctions)}件 / {len(sanctions_by_venue)}競馬場")
        logger.info(f"[INFO] ニュース取得: {len(news)}件 / {len(news_by_venue)}競馬場")
        logger.info(f"[INFO] 生成動画数: {video_count}本")
        if not dry_run:
            for venue, _, vid in sanctions_results:
                if vid:
                    logger.info(f"[INFO] 制裁動画({venue}): https://www.youtube.com/watch?v={vid}")
            for venue, _, vid in news_results:
                if vid:
                    logger.info(f"[INFO] ニュース動画({venue}): https://www.youtube.com/watch?v={vid}")
        else:
            for _, path, _ in all_results:
                if path and Path(path).exists():
                    logger.info(f"[INFO] 動画確認パス: {Path(path).resolve()}")
            logger.info("[INFO] ★ ドライランのため YouTube には投稿していません ★")
        logger.info("[INFO] JRA YouTube Shorts 自動投稿 完了")
        logger.info("=" * 60)

        # GitHub Actions Step Summary
        if not dry_run:
            rows = (
                f"| 日付 | {date_str} |\n"
                f"| 制裁情報 | {len(sanctions)}件 / {len(sanctions_by_venue)}競馬場 |\n"
                f"| ニュース | {len(news)}件 / {len(news_by_venue)}競馬場 |\n"
            )
            for venue, _, vid in sanctions_results:
                if vid:
                    rows += f"| 制裁動画({venue}) | https://www.youtube.com/watch?v={vid} |\n"
            for venue, _, vid in news_results:
                if vid:
                    rows += f"| ニュース動画({venue}) | https://www.youtube.com/watch?v={vid} |\n"
            write_step_summary(
                f"## ✅ JRA Shorts 投稿完了（{uploaded_count}本）\n\n"
                f"| 項目 | 値 |\n|------|----|\n" + rows
            )
        else:
            write_step_summary(
                f"## ✅ JRA Shorts 動画生成完了（ドライラン）\n\n"
                f"| 項目 | 値 |\n|------|----|\n"
                f"| 日付 | {date_str} |\n"
                f"| 制裁情報 | {len(sanctions)}件 / {len(sanctions_by_venue)}競馬場 |\n"
                f"| ニュース | {len(news)}件 / {len(news_by_venue)}競馬場 |\n"
                f"| 生成動画数 | {video_count}本 |\n\n"
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
        if not dry_run:
            audio_files = [f for f in temp_files if f.endswith(".wav")]
            cleanup_temp_files(*audio_files)
        return 1


if __name__ == "__main__":
    sys.exit(main())
