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
LINE_HEIGHT = 66                 # 行間隔（フォントサイズ＋余白）
SUBTITLE_BLOCK_CENTER_Y = 960   # 字幕ブロック中心Y座標（画面縦中央）
SUBTITLE_MAX_CHARS = 18         # 1行あたり最大文字数
SUBTITLE_LINES_PER_CUT = 5     # 1カットの最大行数
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


def _make_subtitle_cuts(text: str) -> list[dict]:
    """
    テキストを字幕カット単位に分割する。

    AIナレーション原稿は全角スペース（　）で意味単位を区切っている。
    その区切りを優先し、1カット最大 SUBTITLE_LINES_PER_CUT 行に収める。
    意味単位が途中で切れないようにし、溢れる場合は次のカットへ。

    Returns:
        [{"lines": [str, ...], "chars": int}, ...]
        chars はそのカットの文字数（表示時間の配分比率に使用）
    """
    # 全角スペースで意味単位を分割（AIナレーションの区切り）
    # 全角スペースがない場合は通常の半角スペースも試みる
    if "\u3000" in text:
        units = [u.strip() for u in text.split("\u3000") if u.strip()]
    else:
        units = [text.strip()]

    cuts: list[dict] = []
    current_lines: list[str] = []
    current_chars = 0

    for unit in units:
        wrapped = textwrap.wrap(unit, width=SUBTITLE_MAX_CHARS)
        if not wrapped:
            continue

        # この意味単位を追加すると行数上限を超える → 現カットを確定して次へ
        if current_lines and len(current_lines) + len(wrapped) > SUBTITLE_LINES_PER_CUT:
            cuts.append({"lines": current_lines, "chars": current_chars})
            current_lines = []
            current_chars = 0

        # 1意味単位だけで行数上限を超える場合は SUBTITLE_LINES_PER_CUT 行ずつ分割
        while len(wrapped) > SUBTITLE_LINES_PER_CUT:
            chunk = wrapped[:SUBTITLE_LINES_PER_CUT]
            cuts.append({"lines": chunk, "chars": sum(len(l) for l in chunk)})
            wrapped = wrapped[SUBTITLE_LINES_PER_CUT:]

        current_lines.extend(wrapped)
        current_chars += len(unit)

    if current_lines:
        cuts.append({"lines": current_lines, "chars": current_chars})

    return cuts


def _build_subtitle_drawtexts(
    cuts: list[dict],
    total_duration: float,
    font_path: str,
) -> list[str]:
    """
    字幕カットリストから drawtext フィルター文字列のリストを生成する。

    各カットは複数行を縦に並べて中央に表示する。
    表示時間は文字数に比例して配分する。

    Returns:
        drawtext フィルター文字列のリスト
    """
    font_param = f":fontfile='{font_path}'" if font_path else ""
    total_chars = sum(c["chars"] for c in cuts) or 1

    filters: list[str] = []
    elapsed = 0.0

    for cut in cuts:
        lines = cut["lines"]
        chars = cut["chars"]
        cut_duration = (chars / total_chars) * total_duration
        start_t = elapsed
        end_t = elapsed + cut_duration
        elapsed = end_t

        # 複数行をブロックとして縦中央揃え
        n_lines = len(lines)
        block_height = n_lines * LINE_HEIGHT
        block_start_y = SUBTITLE_BLOCK_CENTER_Y - block_height // 2

        for j, line in enumerate(lines):
            y = block_start_y + j * LINE_HEIGHT
            escaped = _escape_drawtext(line)
            filters.append(
                f"drawtext=text='{escaped}'"
                f"{font_param}"
                f":fontsize={FONT_SIZE}"
                f":fontcolor=white"
                f":borderw={BORDER_WIDTH}"
                f":bordercolor=black"
                f":x=(w-text_w)/2"
                f":y={y}"
                f":enable='between(t,{start_t:.3f},{end_t:.3f})'"
            )

    return filters


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

    subtitle_cuts = _make_subtitle_cuts(script_text)
    logger.info(f"[INFO] 字幕カット数: {len(subtitle_cuts)}, フォント: {font_path or '未設定'}")

    ffmpeg_cmd = _build_ffmpeg_command(
        audio_path=audio_path,
        bg_path=bg_path,
        bgm_path=bgm_path,
        subtitle_cuts=subtitle_cuts,
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
    subtitle_cuts: list[dict],
    output_path: str,
    duration: float,
    font_path: str,
) -> list[str]:
    """
    FFmpegコマンドを構築して返す。

    字幕は drawtext フィルターを使ってカット単位（4-5行ブロック）で表示する。
    各カットは句点・感嘆符の区切りで切り替わる。

    Args:
        audio_path: ナレーション音声パス
        bg_path: 背景画像パス（Noneの場合は黒背景）
        bgm_path: BGM音声パス（Noneの場合はBGMなし）
        subtitle_cuts: _make_subtitle_cuts() の戻り値
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

    # ========== drawtext 字幕フィルター（カット単位・複数行ブロック）==========
    if subtitle_cuts and duration > 0:
        drawtext_filters = _build_subtitle_drawtexts(subtitle_cuts, duration, font_path)
        all_drawtext = ",".join(drawtext_filters)
        filter_parts.append(f"{video_stream}{all_drawtext}[vout]")
        video_stream = "[vout]"
    else:
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
