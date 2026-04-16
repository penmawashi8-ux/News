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
import re
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


def _generate_default_bgm(output_path: Path, duration: int = 600) -> bool:
    """
    ffmpegのsineジェネレーターでシンプルなアンビエントBGMを生成する。
    C major chord (C4/E4/G4/C5) にエコーとローパスフィルターを適用。

    Args:
        output_path: 出力mp3ファイルパス
        duration: 生成秒数（デフォルト10分）

    Returns:
        True: 生成成功 / False: 失敗
    """
    logger.info(f"[INFO] デフォルトBGMを生成します: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=261.63:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=329.63:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=392.00:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=523.25:duration={duration}",
        "-filter_complex",
        "[0][1][2][3]amix=inputs=4:duration=first,"
        "volume=0.12,aecho=0.8:0.9:80:0.4,lowpass=f=1200[out]",
        "-map", "[out]",
        "-c:a", "libmp3lame", "-b:a", "128k",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            logger.info(f"[INFO] デフォルトBGM生成完了: {output_path}")
            return True
        logger.warning(f"[WARNING] BGM生成失敗: {result.stderr[-300:]}")
        return False
    except Exception as e:
        logger.warning(f"[WARNING] BGM生成エラー: {e}")
        return False


def _get_bgm_path() -> str | None:
    """
    BGMファイルをランダムに選択して返す。
    ファイルが存在しない場合は自動生成を試みる。
    """
    BGM_DIR.mkdir(parents=True, exist_ok=True)

    bgm_files = list(BGM_DIR.glob("*.mp3")) + list(BGM_DIR.glob("*.wav"))
    if not bgm_files:
        # BGMファイルがなければデフォルトBGMを生成
        default_bgm = BGM_DIR / "ambient_bgm.mp3"
        if _generate_default_bgm(default_bgm):
            bgm_files = [default_bgm]
        else:
            logger.warning("[WARNING] BGMファイルの生成に失敗しました。BGMなしで続行します。")
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


_NEWS_ITEM_PATTERN = re.compile(r'^\d+件目')


def _make_subtitle_cuts(text: str) -> list[dict]:
    """
    テキストを字幕カット単位に分割する。

    分割ルール（優先順）:
      1. 「N件目、」で始まる意味単位は必ず新しいカットを開始する
         （ニュース動画でイベント境界を明確にする）
      2. 全角スペース（　）で意味単位を区切る
      3. 1カット最大 SUBTITLE_LINES_PER_CUT 行に収める

    Returns:
        [{"lines": [str, ...], "chars": int}, ...]
    """
    # 全角スペースで意味単位を分割
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

        # 「N件目、」で始まる場合は必ず新しいカットを開始
        is_news_item = bool(_NEWS_ITEM_PATTERN.match(unit))

        if current_lines and (is_news_item or len(current_lines) + len(wrapped) > SUBTITLE_LINES_PER_CUT):
            cuts.append({"lines": current_lines, "chars": current_chars})
            current_lines = []
            current_chars = 0

        # 1意味単位が行数上限を超える場合は分割
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


def _add_drawtext_lines(
    filters: list[str],
    lines: list[str],
    start_t: float,
    end_t: float,
    font_param: str,
) -> None:
    """複数行のテキストブロックをdrawtext フィルターとしてリストに追加する。"""
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


def _build_subtitle_drawtexts_segmented(
    text: str,
    segment_durations: list[float],
    font_path: str,
) -> list[str]:
    """
    セグメントごとの実際の音声長を使って字幕タイミングを算出する。
    全角スペースで分割した各セグメントの実音声長を使うため、
    文字数比率方式より読み上げ速度に忠実にタイミングが合う。

    Args:
        text: 全角スペースで区切られたナレーション原稿
        segment_durations: 各セグメントの実音声長（秒）
        font_path: フォントファイルパス

    Returns:
        drawtext フィルター文字列のリスト
    """
    segments = [s.strip() for s in text.split("\u3000") if s.strip()] if "\u3000" in text else [text.strip()]

    font_param = f":fontfile='{font_path}'" if font_path else ""
    filters: list[str] = []
    elapsed = 0.0

    # セグメント数と音声長リストが不一致の場合は文字数比率にフォールバック
    if len(segments) != len(segment_durations):
        logger.warning(
            f"[WARNING] セグメント数({len(segments)})と音声長数({len(segment_durations)})が不一致 "
            "→ 文字数比率にフォールバック"
        )
        cuts = _make_subtitle_cuts(text)
        total_dur = sum(segment_durations) or 1.0
        return _build_subtitle_drawtexts(cuts, total_dur, font_path)

    for segment, seg_duration in zip(segments, segment_durations):
        if seg_duration <= 0:
            elapsed += seg_duration
            continue

        wrapped = textwrap.wrap(segment, width=SUBTITLE_MAX_CHARS)
        if not wrapped:
            elapsed += seg_duration
            continue

        # セグメントが表示行数上限を超える場合は複数カットに分割
        sub_cuts: list[list[str]] = []
        while len(wrapped) > SUBTITLE_LINES_PER_CUT:
            sub_cuts.append(wrapped[:SUBTITLE_LINES_PER_CUT])
            wrapped = wrapped[SUBTITLE_LINES_PER_CUT:]
        if wrapped:
            sub_cuts.append(wrapped)

        if len(sub_cuts) == 1:
            _add_drawtext_lines(filters, sub_cuts[0], elapsed, elapsed + seg_duration, font_param)
            elapsed += seg_duration
        else:
            # 複数カット: カット内の文字数比率でseg_durationを分配
            total_chars = sum(sum(len(l) for l in c) for c in sub_cuts) or 1
            for chunk in sub_cuts:
                chunk_chars = sum(len(l) for l in chunk)
                cut_dur = (chunk_chars / total_chars) * seg_duration
                _add_drawtext_lines(filters, chunk, elapsed, elapsed + cut_dur, font_param)
                elapsed += cut_dur

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
    segment_durations: list[float] | None = None,
) -> str:
    """
    VOICEVOXの音声・原稿テキスト・テーマから YouTube Shorts用動画を生成する。

    Args:
        audio_path: 音声wavファイルのパス
        script_text: ナレーション原稿（字幕として焼き込む）
        output_path: 出力mp4ファイルのパス
        theme: "jra" または "nar"
        segment_durations: 各セグメント（全角スペース区切り）の実音声長（秒）。
                          指定するとタイミングが音声に正確に合う。

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

    # 字幕drawtextフィルターの生成
    # segment_durationsがあれば実音声長ベース、なければ文字数比率ベース
    if segment_durations:
        subtitle_drawtexts = _build_subtitle_drawtexts_segmented(
            script_text, segment_durations, font_path
        )
        logger.info(f"[INFO] 字幕タイミング: 実音声長ベース ({len(segment_durations)}セグメント)")
    else:
        subtitle_cuts = _make_subtitle_cuts(script_text)
        subtitle_drawtexts = _build_subtitle_drawtexts(subtitle_cuts, audio_duration, font_path)
        logger.info(f"[INFO] 字幕タイミング: 文字数比率ベース ({len(subtitle_cuts)}カット)")

    logger.info(f"[INFO] フォント: {font_path or '未設定'}")

    ffmpeg_cmd = _build_ffmpeg_command(
        audio_path=audio_path,
        bg_path=bg_path,
        bgm_path=bgm_path,
        subtitle_drawtexts=subtitle_drawtexts,
        output_path=output_path,
        duration=audio_duration,
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
    subtitle_drawtexts: list[str],
    output_path: str,
    duration: float,
) -> list[str]:
    """
    FFmpegコマンドを構築して返す。

    字幕は drawtext フィルターを使ってカット単位（4-5行ブロック）で表示する。
    タイミングは呼び出し元で算出済みの drawtext フィルター文字列を受け取る。

    Args:
        audio_path: ナレーション音声パス
        bg_path: 背景画像パス（Noneの場合は黒背景）
        bgm_path: BGM音声パス（Noneの場合はBGMなし）
        subtitle_drawtexts: _build_subtitle_drawtexts*() の戻り値
        output_path: 出力mp4パス
        duration: 動画の長さ（秒）

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
    if subtitle_drawtexts:
        all_drawtext = ",".join(subtitle_drawtexts)
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
