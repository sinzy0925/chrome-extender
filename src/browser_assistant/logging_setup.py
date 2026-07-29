"""ログ出力の最低限セットアップ."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


_CONFIGURED = False


def setup_logging(
    level: str = "INFO",
    *,
    log_file: Path | None = None,
) -> logging.Logger:
    """ルートロガーをコンソール（と任意でファイル）向けに設定する."""
    global _CONFIGURED

    numeric = getattr(logging, level.upper(), None)
    if not isinstance(numeric, int):
        numeric = logging.INFO
        level = "INFO"

    root = logging.getLogger()
    root.setLevel(numeric)

    # 再呼び出し時はハンドラを付け直す（テストでレベル変更を確認しやすくする）
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(numeric)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(numeric)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    _CONFIGURED = True
    logger = logging.getLogger("browser_assistant")
    logger.debug("logging configured: level=%s file=%s", level.upper(), log_file)
    return logger
