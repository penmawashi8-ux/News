"""
ロガーユーティリティ
[INFO] [WARNING] [ERROR] プレフィックス統一のロギング設定
"""

import logging
import os
import sys
from datetime import datetime


def get_logger(name: str) -> logging.Logger:
    """
    統一フォーマットのロガーを取得する。

    Args:
        name: ロガー名（通常は __name__ を渡す）

    Returns:
        設定済みの Logger インスタンス
    """
    logger = logging.getLogger(name)

    # すでにハンドラが設定済みなら再設定しない
    if logger.handlers:
        return logger

    # ログレベルを環境変数から取得（デフォルト: INFO）
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    logger.setLevel(log_level)

    # コンソールハンドラの設定
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    # フォーマット: [LEVEL] YYYY-MM-DD HH:MM:SS - モジュール名 - メッセージ
    formatter = logging.Formatter(
        fmt="[%(levelname)s] %(asctime)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


if __name__ == "__main__":
    # テスト実行
    logger = get_logger(__name__)
    logger.info("ロガーのテスト: INFO メッセージ")
    logger.warning("ロガーのテスト: WARNING メッセージ")
    logger.error("ロガーのテスト: ERROR メッセージ")
    print("✅ ステップ0完了: logger.py テスト成功")
