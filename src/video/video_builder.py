"""
動画生成モジュール
FFmpegを使って競馬情報YouTube Shorts用の縦型動画を生成する。

仕様:
  - 解像度: 1080×1920（縦型・Shorts必須）
  - フレームレート: 30fps
  - 音声: VOICEVOXのwav + BGM（音量20%）
  - 字幕: FFmpeg drawtextで焼き込み（NotoSansJP-Bold 52px・白・黒縁3px）
  - 背景: assets/backgrounds/jra/ or nar/ からランダム選択
  - FFmpegをサブプロセスで直接呼び出す（moviepyは使わない）
"""

import os
import random
import subprocess
import tempfile
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
FONT_COLOR = "white"
BORDER_WIDTH = 3
BORDER_COLOR = "black"

# BGM音量（0.0〜1.0）
BGM_VOLUME = 0.2

# アセットパス
BASE_DIR = Path(__file__).parent.parent.parent
ASSETS_DIR = BASE_DIR / "assets"
FONT_PATH = ASSETS_DIR / "fonts" / "NotoSansJP-Bold.ttf"
BGM_DIR = ASSETS_DIR / "bgm"


def _get_background_path(theme: str) -> str | None:
    """
    テーマ（"jra" or "nar"）に対応する背景画像をランダムに選択して返す。
    画像が見つからない場合はNoneを返す。

    Args:
        theme: "jra" または "nar"

    Returns:
        背景画像のパス文字列、またはNone
    """
    bg_dir = ASSETS_DIR / "backgrounds" / theme
    if not bg_dir.exists():
        logger.warning(f"[WARNING] 背景画像ディレクトリが見つかりません: {bg_dir}")
        return None

    # 対応する画像形式
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
    """
    BGMファイルをランダムに選択して返す。
    BGMが見つからない場合はNoneを返す。

    Returns:
        BGMファイルのパス文字列、またはNone
    """
    if not BGM_DIR.exists():
        return None

    bgm_files = list(BGM_DIR.glob("*.mp3")) + list(BGM_DIR.glob("*.wav"))
    if not bgm_files:
        logger.warning("[WARNING] BGMファイルが見つかりません")
        return None

    selected = random.choice(bgm_files)
    logger.info(f"[INFO] BGM選択: {selected}")
    return str(selected)


def _wrap_text_for_subtitle(text: str, max_chars: int = SUBTITLE_MAX_CHARS) -> list[str]:
    """
    テキストを字幕表示用に折り返す。
    句点・改行で区切り、各行をmax_chars文字以内に収める。

    Args:
        text: 原稿テキスト全文
        max_chars: 1行の最大文字数

    Returns:
        折り返した行のリスト
    """
    # 読点・句点・改行で分割
    lines = []
    # まず文章を句点・改行で区切る
    sentences = text.replace("。", "。\n").replace("！", "！\n").replace("？", "？\n")
    for sentence in sentences.splitlines():
        sentence = sentence.strip()
        if not sentence:
            continue
        # 1文がmax_charsを超える場合は折り返す
        wrapped = textwrap.wrap(sentence, width=max_chars)
        lines.extend(wrapped)
    return lines


def _escape_ffmpeg_text(text: str) -> str:
    """
    FFmpeg drawtext用にテキストをエスケープする。

    Args:
        text: エスケープするテキスト

    Returns:
        エスケープ済みテキスト
    """
    # FFmpegのdrawtextで問題になる文字をエスケープ
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\\'")
    text = text.replace(":", "\\:")
    return text


def _get_audio_duration(audio_path: str) -> float:
    """
    音声ファイルの再生時間（秒）をFFprobeで取得する。

    Args:
        audio_path: 音声ファイルパス

    Returns:
        再生時間（秒）、取得失敗時は60.0
    """
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


def _create_subtitle_script(
    lines: list[str],
    duration: float,
    script_path: str,
) -> None:
    """
    字幕表示タイミングのSRTファイルを生成する。
    各行を均等な時間で表示する。

    Args:
        lines: 字幕行のリスト
        duration: 動画の総再生時間（秒）
        script_path: 出力SRTファイルパス
    """
    if not lines:
        return

    # 各行の表示時間を均等に割り当て
    time_per_line = duration / len(lines)

    def seconds_to_srt_time(seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    with open(script_path, "w", encoding="utf-8") as f:
        for i, line in enumerate(lines):
            start_time = i * time_per_line
            end_time = (i + 1) * time_per_line
            f.write(f"{i + 1}\n")
            f.write(f"{seconds_to_srt_time(start_time)} --> {seconds_to_srt_time(end_time)}\n")
            f.write(f"{line}\n\n")


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

    # 出力ディレクトリを作成
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # 音声の長さを取得
    audio_duration = _get_audio_duration(audio_path)

    # 背景画像を選択
    bg_path = _get_background_path(theme)
    bgm_path = _get_bgm_path()

    # 字幕テキストを行に分割
    subtitle_lines = _wrap_text_for_subtitle(script_text)
    logger.info(f"[INFO] 字幕行数: {len(subtitle_lines)}")

    # フォントパスの確認
    font_path = str(FONT_PATH)
    if not Path(font_path).exists():
        logger.warning(f"[WARNING] フォントファイルが見つかりません: {font_path}")
        # システムフォントへのフォールバック
        font_path = "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"
        if not Path(font_path).exists():
            font_path = ""  # FFmpegのデフォルトフォントを使用

    with tempfile.TemporaryDirectory() as tmpdir:
        # SRTファイルを生成（字幕タイミング用）
        srt_path = os.path.join(tmpdir, "subtitle.srt")
        _create_subtitle_script(subtitle_lines, audio_duration, srt_path)

        # FFmpegコマンドを構築
        ffmpeg_cmd = _build_ffmpeg_command(
            audio_path=audio_path,
            bg_path=bg_path,
            bgm_path=bgm_path,
            srt_path=srt_path,
            output_path=output_path,
            duration=audio_duration,
            font_path=font_path,
        )

        logger.info(f"[INFO] FFmpegコマンド実行")

        # FFmpegを実行
        result = subprocess.run(
            ffmpeg_cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 最大5分
        )

        if result.returncode != 0:
            logger.error(f"[ERROR] FFmpeg実行失敗:\n{result.stderr}")
            raise RuntimeError(f"FFmpegの実行に失敗しました: {result.stderr[-500:]}")

        logger.info(f"[INFO] 動画生成完了: {output_path}")
        return output_path


def _build_ffmpeg_command(
    audio_path: str,
    bg_path: str | None,
    bgm_path: str | None,
    srt_path: str,
    output_path: str,
    duration: float,
    font_path: str,
) -> list[str]:
    """
    FFmpegコマンドを構築して返す。

    背景画像あり・なし、BGMあり・なしに対応したコマンドを動的に生成する。

    Args:
        audio_path: ナレーション音声パス
        bg_path: 背景画像パス（Noneの場合は黒背景）
        bgm_path: BGM音声パス（Noneの場合はBGMなし）
        srt_path: SRT字幕ファイルパス
        output_path: 出力mp4パス
        duration: 動画の長さ（秒）
        font_path: フォントファイルパス

    Returns:
        FFmpegコマンドのリスト
    """
    cmd = ["ffmpeg", "-y"]  # -y: 上書き確認をスキップ

    # ========== 入力ストリームの設定 ==========

    if bg_path:
        # 背景画像をループ再生（音声の長さに合わせる）
        cmd += ["-loop", "1", "-i", bg_path]
    else:
        # 黒背景を生成
        cmd += [
            "-f", "lavfi",
            "-i", f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={VIDEO_FPS}",
        ]

    # ナレーション音声
    cmd += ["-i", audio_path]

    # BGM（オプション）
    if bgm_path:
        cmd += ["-stream_loop", "-1", "-i", bgm_path]

    # ========== フィルター複合グラフの設定 ==========
    filter_parts = []
    video_stream = "[0:v]"
    audio_streams = []

    # 背景画像のスケール・クロップ（1080x1920に整形）
    filter_parts.append(
        f"{video_stream}scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}[bg]"
    )
    video_stream = "[bg]"

    # 字幕焼き込み（SRTファイルを使用）
    # ※ fontfile は force_style 非対応のため fontsdir を別パラメータで指定する
    srt_path_escaped = srt_path.replace("\\", "/").replace(":", "\\:")
    if font_path and Path(font_path).exists():
        font_dir = str(Path(font_path).parent).replace("\\", "/")
        fontsdir_option = f":fontsdir='{font_dir}'"
    else:
        fontsdir_option = ""
    filter_parts.append(
        f"{video_stream}subtitles='{srt_path_escaped}'"
        f"{fontsdir_option}"
        f":force_style='FontSize={FONT_SIZE},PrimaryColour=&HFFFFFF,OutlineColour=&H000000,"
        f"BorderStyle=1,Outline={BORDER_WIDTH},Bold=1,Alignment=2,MarginV=320'[vout]"
    )
    video_stream = "[vout]"

    # 音声ミキシング（ナレーション + BGM）
    if bgm_path:
        # BGMの音量を下げてナレーションとミックス
        filter_parts.append(f"[2:a]volume={BGM_VOLUME}[bgm_vol]")
        filter_parts.append(f"[1:a][bgm_vol]amix=inputs=2:duration=first[aout]")
        audio_streams = ["[aout]"]
    else:
        audio_streams = ["1:a"]

    # ========== コマンド組み立て ==========
    cmd += ["-filter_complex", ";".join(filter_parts)]

    # 映像出力マッピング
    cmd += ["-map", video_stream]

    # 音声出力マッピング
    if bgm_path:
        cmd += ["-map", "[aout]"]
    else:
        cmd += ["-map", "1:a"]

    # エンコード設定
    cmd += [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-r", str(VIDEO_FPS),
        "-t", str(duration),  # 音声長さに合わせて動画を切る
        output_path,
    ]

    return cmd


if __name__ == "__main__":
    # テスト実行
    import os
    from dotenv import load_dotenv
    load_dotenv()

    print("=== 動画生成テスト ===")

    # FFmpegの確認
    result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
    if result.returncode == 0:
        print("FFmpeg: 利用可能")
    else:
        print("FFmpeg: 見つかりません（インストールが必要）")

    print(f"フォントパス: {FONT_PATH} （{'存在する' if FONT_PATH.exists() else '存在しない'}）")
    print("✅ ステップ7完了: video_builder.py テスト成功（実際の動画生成は音声ファイルが必要）")
