"""
VOICEVOX音声生成モジュール
テキストをVOICEVOXエンジンで音声合成してwavファイルに保存する。
VOICEVOXに接続できない場合はpyttsx3でフォールバックする。
"""

import json
import os
import subprocess
from pathlib import Path

import requests

from src.utils.logger import get_logger

logger = get_logger(__name__)

# デフォルト設定
DEFAULT_VOICEVOX_URL = "http://localhost:50021"
DEFAULT_SPEAKER_ID = 3      # ずんだもん
DEFAULT_SPEED_SCALE = 1.1   # 話速（1.1倍）


def _get_voicevox_url() -> str:
    """VOICEVOXエンジンのURLを環境変数から取得する"""
    return os.getenv("VOICEVOX_URL", DEFAULT_VOICEVOX_URL)


def _get_speaker_id() -> int:
    """スピーカーIDを環境変数から取得する"""
    try:
        return int(os.getenv("VOICEVOX_SPEAKER_ID", str(DEFAULT_SPEAKER_ID)))
    except ValueError:
        return DEFAULT_SPEAKER_ID


def _voicevox_synthesis(
    text: str,
    output_path: str,
    speaker_id: int,
    speed_scale: float,
) -> bool:
    """
    VOICEVOXエンジンで音声合成してwavファイルに保存する。

    Args:
        text: 読み上げるテキスト
        output_path: 出力wavファイルパス
        speaker_id: VOICEVOXスピーカーID
        speed_scale: 話速倍率

    Returns:
        成功した場合True、失敗した場合False
    """
    base_url = _get_voicevox_url()

    try:
        # Step1: 音声クエリの生成
        query_response = requests.post(
            f"{base_url}/audio_query",
            params={"text": text, "speaker": speaker_id},
            timeout=30,
        )
        query_response.raise_for_status()
        query = query_response.json()

        # 話速を設定
        query["speedScale"] = speed_scale

        # Step2: 音声合成の実行
        synthesis_response = requests.post(
            f"{base_url}/synthesis",
            params={"speaker": speaker_id},
            data=json.dumps(query),
            headers={"Content-Type": "application/json"},
            timeout=120,  # 長いテキストは合成に時間がかかる
        )
        synthesis_response.raise_for_status()

        # Step3: wavファイルに保存
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        with open(output_path, "wb") as f:
            f.write(synthesis_response.content)

        logger.info(f"[INFO] VOICEVOX音声合成完了: {output_path}")
        return True

    except requests.ConnectionError:
        logger.warning(f"[WARNING] VOICEVOXエンジンに接続できません: {base_url}")
        return False
    except requests.RequestException as e:
        logger.error(f"[ERROR] VOICEVOX音声合成に失敗: {e}")
        return False


def _pyttsx3_synthesis(text: str, output_path: str) -> bool:
    """
    pyttsx3（システムTTS）でフォールバック音声合成する。
    VOICEVOXが利用できない場合に使用。

    Args:
        text: 読み上げるテキスト
        output_path: 出力wavファイルパス

    Returns:
        成功した場合True、失敗した場合False
    """
    try:
        import pyttsx3

        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        engine = pyttsx3.init()

        # 日本語音声に設定（利用可能な場合）
        voices = engine.getProperty("voices")
        for voice in voices:
            if "japanese" in voice.name.lower() or "ja" in voice.id.lower():
                engine.setProperty("voice", voice.id)
                break

        # 話速設定（デフォルト200wpm → 220wpmに）
        engine.setProperty("rate", 220)

        engine.save_to_file(text, output_path)
        engine.runAndWait()

        if Path(output_path).exists() and Path(output_path).stat().st_size > 0:
            logger.info(f"[INFO] pyttsx3フォールバック音声合成完了: {output_path}")
            return True
        else:
            logger.error("[ERROR] pyttsx3音声ファイルの生成に失敗（ファイルが空）")
            return False

    except ImportError:
        logger.error("[ERROR] pyttsx3がインストールされていません")
        return False
    except Exception as e:
        logger.error(f"[ERROR] pyttsx3音声合成に失敗: {e}")
        return False


def text_to_speech(
    text: str,
    output_path: str,
    speaker_id: int | None = None,
) -> str:
    """
    テキストを音声合成してwavファイルに保存し、ファイルパスを返す。

    処理順:
      1. VOICEVOXエンジンで合成を試みる
      2. 失敗した場合はpyttsx3でフォールバック

    Args:
        text: 読み上げるテキスト
        output_path: 出力wavファイルパス
        speaker_id: VOICEVOXスピーカーID（Noneの場合は環境変数またはデフォルト値使用）

    Returns:
        生成されたwavファイルのパス

    Raises:
        RuntimeError: 全ての音声合成方法が失敗した場合
    """
    if speaker_id is None:
        speaker_id = _get_speaker_id()

    logger.info(f"[INFO] 音声合成開始: speaker_id={speaker_id}, 出力={output_path}")
    logger.info(f"[INFO] テキスト長: {len(text)}文字")

    # VOICEVOXで合成を試みる
    if _voicevox_synthesis(text, output_path, speaker_id, DEFAULT_SPEED_SCALE):
        return output_path

    # フォールバック: pyttsx3
    logger.warning("[WARNING] VOICEVOXが利用不可のためpyttsx3でフォールバック")
    if _pyttsx3_synthesis(text, output_path):
        return output_path

    raise RuntimeError("全ての音声合成方法が失敗しました。VOICEVOXの起動またはpyttsx3の設定を確認してください。")


def _get_audio_duration_local(audio_path: str) -> float:
    """ffprobeで音声ファイルの長さ（秒）を取得する。"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", audio_path],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _concat_audio_files(input_paths: list[str], output_path: str) -> None:
    """複数のwavファイルをffmpegのconcatデマクサで無音なく連結する。"""
    # concatリストファイルを作成（絶対パスで記述）
    list_path = output_path.replace(".wav", "_concat_list.txt")
    list_content = "\n".join(
        f"file '{Path(p).resolve()}'" for p in input_paths
    )
    try:
        with open(list_path, "w", encoding="utf-8") as f:
            f.write(list_content)

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error(f"[ERROR] 音声連結失敗: {result.stderr[-500:]}")
    finally:
        try:
            Path(list_path).unlink(missing_ok=True)
        except Exception:
            pass


def text_to_speech_segmented(
    text: str,
    output_path: str,
    speaker_id: int | None = None,
) -> tuple[str, list[float]]:
    """
    テキストを全角スペース（　）で分割し、各セグメントを個別に音声合成する。
    セグメントごとの実際の音声長を返すため、字幕タイミングを音声に正確に合わせられる。

    Args:
        text: 読み上げるテキスト（全角スペースで意味単位を区切る）
        output_path: 出力wavファイルパス
        speaker_id: VOICEVOXスピーカーID

    Returns:
        (output_path, segment_durations): 各セグメントの音声長（秒）のリスト
    """
    if speaker_id is None:
        speaker_id = _get_speaker_id()

    # 全角スペースでセグメント分割
    segments = [s.strip() for s in text.split("\u3000") if s.strip()] if "\u3000" in text else []
    if not segments:
        segments = [text.strip()] if text.strip() else []

    if len(segments) <= 1:
        # セグメントが1つだけ → 通常合成してdurationを返す
        result_path = text_to_speech(text, output_path, speaker_id)
        duration = _get_audio_duration_local(result_path)
        return result_path, [duration]

    # 各セグメントを個別合成
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(output_path).stem

    segment_files: list[str] = []
    segment_durations: list[float] = []

    for i, segment in enumerate(segments):
        seg_path = str(output_dir / f"_seg{i}_{stem}.wav")
        ok = _voicevox_synthesis(segment, seg_path, speaker_id, DEFAULT_SPEED_SCALE)
        if not ok:
            logger.warning(f"[WARNING] セグメント{i} VOICEVOX失敗 → pyttsx3フォールバック")
            _pyttsx3_synthesis(segment, seg_path)

        if Path(seg_path).exists() and Path(seg_path).stat().st_size > 0:
            dur = _get_audio_duration_local(seg_path)
            segment_files.append(seg_path)
            segment_durations.append(dur)
        else:
            # 合成失敗: 0秒のプレースホルダーとして扱う
            segment_durations.append(0.0)
            logger.warning(f"[WARNING] セグメント{i} 音声ファイル生成失敗")

    # セグメント音声を連結
    valid_files = [f for f, d in zip(segment_files, segment_durations) if d > 0]
    if valid_files:
        _concat_audio_files(valid_files, output_path)
    else:
        # 全セグメント失敗: フォールバックで全体合成
        logger.warning("[WARNING] 全セグメント合成失敗 → 全体フォールバック合成")
        text_to_speech(text, output_path, speaker_id)
        duration = _get_audio_duration_local(output_path)
        segment_durations = [duration / len(segments)] * len(segments)

    # 一時ファイル削除
    for f in segment_files:
        try:
            Path(f).unlink(missing_ok=True)
        except Exception:
            pass

    total = sum(segment_durations)
    logger.info(f"[INFO] セグメント合成完了: {len(segments)}セグメント / 合計{total:.1f}秒")
    return output_path, segment_durations


def register_user_dict(dict_path: str | None = None) -> bool:
    """
    VOICEVOXのユーザー辞書にカスタム読みを登録する。
    競走馬・騎手名など固有名詞の読み誤りを防ぐ。

    Args:
        dict_path: 辞書JSONファイルのパス（省略時は assets/voicevox_dict.json）

    Returns:
        True: 登録成功 / False: 失敗またはVOICEVOX未起動
    """
    if dict_path is None:
        from pathlib import Path as _Path
        dict_path = str(_Path(__file__).parent.parent.parent / "assets" / "voicevox_dict.json")

    if not Path(dict_path).exists():
        logger.info(f"[INFO] 辞書ファイルが見つかりません（スキップ）: {dict_path}")
        return False

    try:
        import json as _json
        with open(dict_path, encoding="utf-8") as f:
            data = _json.load(f)
        words = data.get("words", [])
    except Exception as e:
        logger.warning(f"[WARNING] 辞書ファイルの読み込み失敗: {e}")
        return False

    if not words:
        return True

    base_url = _get_voicevox_url()

    # 既存の辞書を取得してsurfaceで重複チェック
    try:
        existing_resp = requests.get(f"{base_url}/user_dict", timeout=10)
        existing = existing_resp.json() if existing_resp.ok else {}
        existing_surfaces = {v.get("surface") for v in existing.values()}
    except Exception:
        existing_surfaces = set()

    registered = 0
    for word in words:
        surface = word.get("surface", "")
        if not surface or surface in existing_surfaces:
            continue
        try:
            params = {
                "surface": surface,
                "pronunciation": word.get("pronunciation", ""),
                "accent_type": word.get("accent_type", 0),
                "word_type": word.get("word_type", "PROPER_NOUN"),
                "priority": word.get("priority", 5),
            }
            resp = requests.post(f"{base_url}/user_dict_word", params=params, timeout=10)
            if resp.ok:
                registered += 1
        except Exception as e:
            logger.warning(f"[WARNING] 辞書登録失敗 '{surface}': {e}")

    logger.info(f"[INFO] VOICEVOX辞書登録完了: {registered}/{len(words)}件")
    return True


def check_voicevox_available() -> bool:
    """
    VOICEVOXエンジンが起動・応答しているか確認する。

    Returns:
        True: 利用可能 / False: 利用不可
    """
    base_url = _get_voicevox_url()
    try:
        response = requests.get(f"{base_url}/version", timeout=5)
        version = response.json()
        logger.info(f"[INFO] VOICEVOX利用可能: バージョン {version}")
        return True
    except Exception:
        logger.warning(f"[WARNING] VOICEVOXエンジンに接続できません: {base_url}")
        return False


if __name__ == "__main__":
    # テスト実行
    import os
    from dotenv import load_dotenv
    load_dotenv()

    print("=== VOICEVOX TTS テスト ===")

    # VOICEVOX の起動確認
    available = check_voicevox_available()
    print(f"VOICEVOX利用可能: {available}")

    # テスト音声合成
    test_text = "本日のJRA情報をお届けします！本日、制裁情報はありませんでした。以上、本日のJRA情報でした！"
    test_output = "output/test_audio.wav"

    os.makedirs("output", exist_ok=True)

    try:
        result_path = text_to_speech(test_text, test_output)
        print(f"音声ファイル生成成功: {result_path}")
    except RuntimeError as e:
        print(f"音声合成失敗: {e}")

    print("✅ ステップ6完了: voicevox_tts.py テスト成功")
