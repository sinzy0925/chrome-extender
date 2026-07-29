"""初回起動・配布時の事前チェック."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from browser_assistant.browser import find_chrome_executable, is_cdp_available
from browser_assistant.config import ConfigError, load_settings
from browser_assistant.paths import get_app_dir, get_env_path


@dataclass
class CheckItem:
    ok: bool
    code: str
    message: str


def ensure_env_example(app_dir: Path | None = None) -> Path | None:
    """exe/アプリ横に .env.example が無ければ、同梱テンプレからコピーを試みる."""
    import shutil
    import sys

    base = app_dir if app_dir is not None else get_app_dir()
    dest = base / ".env.example"
    if dest.is_file():
        return dest

    candidates: list[Path] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / ".env.example")  # type: ignore[attr-defined]
    # 開発時
    candidates.append(Path(__file__).resolve().parents[2] / ".env.example")

    for src in candidates:
        if src.is_file():
            try:
                shutil.copy2(src, dest)
                return dest
            except OSError:
                continue
    return None


def run_startup_checks(
    *,
    app_dir: Path | None = None,
    require_api_key: bool = True,
    check_chrome: bool = True,
) -> list[CheckItem]:
    """`.env` / APIキー / Chrome の状態を確認し、案内用の結果を返す."""
    base = app_dir if app_dir is not None else get_app_dir()
    results: list[CheckItem] = []

    ensure_env_example(base)
    env_path = get_env_path(base)

    if not env_path.is_file():
        results.append(
            CheckItem(
                ok=False,
                code="missing_env",
                message=(
                    f"`.env` がありません。`.env.example` をコピーして `{env_path.name}` を作成し、"
                    "ご自身で取得した Gemini API キーを記入してください。\n"
                    f"場所: {env_path}"
                ),
            )
        )
        # 以降の設定読込はできない
    else:
        results.append(
            CheckItem(ok=True, code="env_found", message=f"`.env` を検出: {env_path}")
        )
        try:
            settings = load_settings(app_dir=base, require_api_key=require_api_key)
            if require_api_key and not settings.gemini_api_key.strip():
                results.append(
                    CheckItem(
                        ok=False,
                        code="empty_api_key",
                        message="GEMINI_API_KEY が空です。.env にキーを記入してください。",
                    )
                )
            elif not settings.gemini_api_key.strip():
                results.append(
                    CheckItem(
                        ok=False,
                        code="empty_api_key",
                        message="GEMINI_API_KEY が空です（ブラウザ確認のみなら続行可）。",
                    )
                )
            else:
                results.append(
                    CheckItem(ok=True, code="api_key", message="GEMINI_API_KEY が設定されています。")
                )
        except ConfigError as exc:
            results.append(CheckItem(ok=False, code="config_error", message=str(exc)))

    if check_chrome:
        chrome = find_chrome_executable()
        if chrome is None:
            results.append(
                CheckItem(
                    ok=False,
                    code="chrome_missing",
                    message=(
                        "Google Chrome が見つかりません。"
                        "インストールするか、.env の CHROME_PATH に chrome.exe のフルパスを設定してください。"
                        "（本アプリはシステムに入っている Chrome を CDP で利用します。Chromium は同梱しません。）"
                    ),
                )
            )
        else:
            results.append(
                CheckItem(ok=True, code="chrome_found", message=f"Chrome を検出: {chrome}")
            )

    return results


def format_startup_report(items: list[CheckItem]) -> str:
    lines = ["=== 起動チェック ==="]
    for item in items:
        mark = "OK" if item.ok else "NG"
        lines.append(f"[{mark}] {item.message}")
    return "\n".join(lines)


def startup_ok(items: list[CheckItem], *, require_all: bool = True) -> bool:
    if require_all:
        return all(i.ok for i in items)
    return any(i.code == "env_found" and i.ok for i in items)
