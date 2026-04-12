"""
動画生成モジュール
FFmpegを使って競馬情報YouTube Shorts用の縦型動画を生成する。

仕様:
  - 解像度: 1080×1920（縦型・Shorts必須）
  - フレームレート: 30fps
  - 音声: VOICEVOXのwav + BGM（音量20%）
  - 字幕: FFmpeg drawtext で焼き込み（NotoSansJP-Bold 52px・白・黒縁3px）
          subtitles フィルターは使わず drawtext を直接使用（フォント指定が確実）
  - 背景: assets/backgrounds/jra/ or nar/ からランダム選択・1080x1920にクロップ
  - FFmpegをサブプロセスで直接呼び出す（moviepyは使わない）
"""

import os
import random
import subprocess
import textwrap
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 動画仕様
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 30

# 字幕設定
FONT_SIZE = 52
SUBTITLE_Y = 1600           # 字幕表示Y座標（下部）
SUBTITLE_MAX_CHARS = 18     # 1行あたり最大文字数
BORDER_WIDTH = 3

# BGM音量（0.0〜1.0）
BGM_VOLUME = 0.2

# アセットパス
BASE_DIR = Path(__file__).parent.parent.parent
ASSETS_DIR = BASE_DIR / "assets"
FONT_PATH = ASSETS_DIR / "fonts" / "NotoSansJP-Bold.ttf"
BGM_DIR = ASSETS_DIR / "bgm"

# GitHub Actions / Linux 環境でのシステムフォント候補（日本語対応）
SYSTEM_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _get_background_path(theme: str) -> str | None:
    """
    テーマ（"jra" or "nar"）に対応する背景画像をランダムに選択して返す。
    画像が見つからない場合はNoneを返す。
    """
    bg_dir = ASSETS_DIR / "backgrounds" / theme
    if not bg_dir.exists():
        logger.warning(f"[WARNING] 背景画像ディレクトリが見つかりません: {bg_dir}")
        return None

    extensions = ["*.jpg", "*.jpeg", "*.png", "*.bmp"]
    images = []
    for ext in extensions:
        images.extend(bg_dir.glob(ext))

    if not images:
        logger.warning(f"[WARNING] 背景画像が見つかりません: {bg_dir}")
        return None

    selected = random.choice(images)
    logger.info(f"[INFO] 背景画像選択: {selected}")
    return str(selected)


def _get_bgm_path() -> str | None:
    """BGMファイルをランダムに選択して返す。"""
    if not BGM_DIR.exists():
        return None

    bgm_files = list(BGM_DIR.glob("*.mp3")) + list(BGM_DIR.glob("*.wav"))
    if not bgm_files:
        logger.warning("[WARNING] BGMファイルが見つかりません")
        return None

    selected = random.choice(bgm_files)
    logger.info(f"[INFO] BGM選択: {selected}")
    return str(selected)


def _resolve_font_path() -> str:
    """
    使用するフォントファイルのパスを解決する。
    プロジェクト内フォント → システムフォント の順で探す。

    Returns:
        フォントファイルのパス文字列（見つからない場合は空文字）
    """
    # まずプロジェクト内のフォントを確認
    if FONT_PATH.exists():
        logger.info(f"[INFO] フォント使用: {FONT_PATH}")
        return str(FONT_PATH)

    # システムフォントを探す
    for candidate in SYSTEM_FONT_CANDIDATES:
        if Path(candidate).exists():
            logger.info(f"[INFO] システムフォント使用: {candidate}")
            return candidate

    # fc-listで日本語フォントを探す（Linux環境）
    try:
        result = subprocess.run(
            ["fc-list", ":lang=ja", "--format=%{file}\n"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.strip().splitlines():
            font_file = line.strip()
            if font_file and Path(font_file).exists():
                logger.info(f"[INFO] fc-listで発見したフォント: {font_file}")
                return font_file
    except Exception:
        pass

    logger.warning("[WARNING] 日本語フォントが見つかりません。字幕が文字化けする可能性があります")
    return ""


def _wrap_text_for_subtitle(text: str, max_chars: int = SUBTITLE_MAX_CHARS) -> list[str]:
    """
    テキストを字幕表示用に折り返す。
    句点・感嘆符・改行で区切り、各行をmax_chars文字以内に収める。

    Args:
        text: 原稿テキスト全文
        max_chars: 1行の最大文字数

    Returns:
        折り返した行のリスト
    """
    lines = []
    sentences = text.replace("。", "。\n").replace("！", "！\n").replace("？", "？\n")
    for sentence in sentences.splitlines():
        sentence = sentence.strip()
        if not sentence:
            continue
        wrapped = textwrap.wrap(sentence, width=max_chars)
        lines.extend(wrapped)
    return lines


def _escape_drawtext(text: str) -> str:
    """
    FFmpeg drawtext フィルター用にテキストをエスケープする。

    Args:
        text: エスケープするテキスト

    Returns:
        エスケープ済みテキスト
    """
    text = text.replace("\\", "\\\\")   # バックスラッシュ
    text = text.replace("'", "\u2019")  # シングルクォートを右シングルクォートに置換
    text = text.replace(":", "\\:")     # コロン
    text = text.replace("%", "\\%")     # パーセント
    text = text.replace("[", "\\[")     # 角括弧
    text = text.replace("]", "\\]")
    return text


def _get_audio_duration(audio_path: str) -> float:
    """音声ファイルの再生時間（秒）をFFprobeで取得する。"""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                audio_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        duration = float(result.stdout.strip())
        logger.info(f"[INFO] 音声ファイル長: {duration:.2f}秒")
        return duration
    except Exception as e:
        logger.warning(f"[WARNING] 音声ファイル長の取得失敗、デフォルト60秒を使用: {e}")
        return 60.0


def build_video(
    audio_path: str,
    script_text: str,
    output_path: str,
    theme: str = "jra",
) -> str:
    """
    VOICEVOXの音声・原稿テキスト・テーマから YouTube Shorts用動画を生成する。

    Args:
        audio_path: 音声wavファイルのパス
        script_text: ナレーション原稿（字幕として焼き込む）
        output_path: 出力mp4ファイルのパス
        theme: "jra" または "nar"

    Returns:
        生成された動画ファイルのパス

    Raises:
        RuntimeError: 動画生成に失敗した場合
    """
    logger.info(f"[INFO] 動画生成開始: theme={theme}, 出力={output_path}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    audio_duration = _get_audio_duration(audio_path)
    bg_path = _get_background_path(theme)
    bgm_path = _get_bgm_path()
    font_path = _resolve_font_path()

    subtitle_lines = _wrap_text_for_subtitle(script_text)
    logger.info(f"[INFO] 字幕行数: {len(subtitle_lines)}, フォント: {font_path or '未設定'}")

    ffmpeg_cmd = _build_ffmpeg_command(
        audio_path=audio_path,
        bg_path=bg_path,
        bgm_path=bgm_path,
        subtitle_lines=subtitle_lines,
        output_path=output_path,
        duration=audio_duration,
        font_path=font_path,
    )

    logger.info("[INFO] FFmpegコマンド実行")

    result = subprocess.run(
        ffmpeg_cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        logger.error(f"[ERROR] FFmpeg実行失敗:\n{result.stderr[-1000:]}")
        raise RuntimeError(f"FFmpegの実行に失敗しました: {result.stderr[-500:]}")

    logger.info(f"[INFO] 動画生成完了: {output_path}")
    return output_path


def _build_ffmpeg_command(
    audio_path: str,
    bg_path: str | None,
    bgm_path: str | None,
    subtitle_lines: list[str],
    output_path: str,
    duration: float,
    font_path: str,
) -> list[str]:
    """
    FFmpegコマンドを構築して返す。

    字幕は drawtext フィルターを使って各行を時間ベースで表示する。
    subtitles フィルターは使わない（フォント指定の互換性問題を回避）。

    Args:
        audio_path: ナレーション音声パス
        bg_path: 背景画像パス（Noneの場合は黒背景）
        bgm_path: BGM音声パス（Noneの場合はBGMなし）
        subtitle_lines: 字幕行のリスト
        output_path: 出力mp4パス
        duration: 動画の長さ（秒）
        font_path: フォントファイルパス（空文字の場合はデフォルト）

    Returns:
        FFmpegコマンドのリスト
    """
    cmd = ["ffmpeg", "-y"]

    # ========== 入力ストリーム ==========
    if bg_path:
        cmd += ["-loop", "1", "-i", bg_path]
    else:
        # 背景なし: 競馬らしいグラデーション（緑→黒）で代替
        cmd += [
            "-f", "lavfi",
            "-i", f"color=c=0x1a4a1a:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={VIDEO_FPS}",
        ]

    cmd += ["-i", audio_path]

    if bgm_path:
        cmd += ["-stream_loop", "-1", "-i", bgm_path]

    # ========== フィルター複合グラフ ==========
    filter_parts = []
    video_stream = "[0:v]"

    # 背景スケール・クロップ
    filter_parts.append(
        f"{video_stream}scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}[bg]"
    )
    video_stream = "[bg]"

    # ========== drawtext 字幕フィルター ==========
    # 各行を均等な時間で表示する
    if subtitle_lines and duration > 0:
        time_per_line = duration / len(subtitle_lines)
        font_param = f":fontfile='{font_path}'" if font_path else ""

        # 各字幕行を drawtext で順番に表示
        drawtext_filters = []
        for i, line in enumerate(subtitle_lines):
            start_t = i * time_per_line
            end_t = (i + 1) * time_per_line
            escaped = _escape_drawtext(line)

            drawtext_filters.append(
                f"drawtext=text='{escaped}'"
                f"{font_param}"
                f":fontsize={FONT_SIZE}"
                f":fontcolor=white"
                f":borderw={BORDER_WIDTH}"
                f":bordercolor=black"
                f":x=(w-text_w)/2"          # 横中央揃え
                f":y={SUBTITLE_Y}"
                f":enable='between(t,{start_t:.3f},{end_t:.3f})'"
            )

        # drawtext をチェーンでつなぐ（[bg] → drawtext1 → drawtext2 → ... → [vout]）
        all_drawtext = ",".join(drawtext_filters)
        filter_parts.append(f"{video_stream}{all_drawtext}[vout]")
        video_stream = "[vout]"
    else:
        # 字幕なし: そのまま出力
        filter_parts.append(f"{video_stream}copy[vout]")
        video_stream = "[vout]"

    # ========== 音声ミキシング ==========
    if bgm_path:
        filter_parts.append(f"[2:a]volume={BGM_VOLUME}[bgm_vol]")
        filter_parts.append(f"[1:a][bgm_vol]amix=inputs=2:duration=first[aout]")

    # ========== コマンド組み立て ==========
    cmd += ["-filter_complex", ";".join(filter_parts)]
    cmd += ["-map", video_stream]
    cmd += ["-map", "[aout]" if bgm_path else "1:a"]
    cmd += [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-r", str(VIDEO_FPS),
        "-t", str(duration),
        output_path,
    ]

    return cmd


if __name__ == "__main__":
    import subprocess as sp

    print("=== 動画生成テスト ===")

    result = sp.run(["ffmpeg", "-version"], capture_output=True, text=True)
    print(f"FFmpeg: {'利用可能' if result.returncode == 0 else '見つかりません'}")

    font = _resolve_font_path()
    print(f"フォント: {font or '見つかりません（字幕が正しく表示されない可能性あり）'}")

    print("✅ ステップ7完了: video_builder.py テスト成功")
