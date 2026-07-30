"""アプリ／exe 基準のパス解決."""

from __future__ import annotations

import sys
from pathlib import Path


def get_app_dir() -> Path:
    """設定ファイル（.env）を置くディレクトリを返す.

    - PyInstaller 等で freeze された場合: exe と同じディレクトリ
    - 開発時: リポジトリルート（src/ の親）
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    # src/browser_assistant/paths.py -> parents[0]=package, [1]=src, [2]=repo root
    return Path(__file__).resolve().parents[2]


def get_env_path(app_dir: Path | None = None) -> Path:
    """アプリ同階層の .env パス."""
    base = app_dir if app_dir is not None else get_app_dir()
    return base / ".env"


def resolve_under_app(path: str | Path, app_dir: Path | None = None) -> Path:
    """相対パスならアプリディレクトリ基準、絶対パスならそのまま."""
    base = app_dir if app_dir is not None else get_app_dir()
    p = Path(path)
    if p.is_absolute():
        return p
    return (base / p).resolve()


DEFAULT_LOG_FILENAME = "browser_assistant.log"


def get_default_log_path(app_dir: Path | None = None) -> Path:
    """アプリ同階層の固定ログパス（log/browser_assistant.log）."""
    base = app_dir if app_dir is not None else get_app_dir()
    return base / "log" / DEFAULT_LOG_FILENAME
