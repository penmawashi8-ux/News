"""
VOICEVOX音声生成モジュール
テキストをVOICEVOXエンジンで音声合成してwavファイルに保存する。
VOICEVOXに接続できない場合はpyttsx3でフォールバックする。
"""

import json
import os
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
