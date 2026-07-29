"""`.env` からの設定読込."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from browser_assistant.paths import get_app_dir, get_env_path, resolve_under_app


class ConfigError(Exception):
    """設定不備（ユーザー向けメッセージを持つ）."""


@dataclass(frozen=True)
class Settings:
    """ランタイム設定."""

    gemini_api_key: str
    gemini_model_flash: str
    gemini_model_flash_lite: str
    cdp_host: str
    cdp_port: int
    user_data_dir: Path
    log_level: str
    app_dir: Path
    env_path: Path
    chrome_path: str | None = None
    keep_browser_open: bool = True
    observe_max_candidates: int = 40
    max_steps: int = 30
    post_action_wait_ms: int = 500
    max_consecutive_failures: int = 2
    replan_on_failure: bool = True
    collect_mode: bool = False


def _require_non_empty(name: str, value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        raise ConfigError(
            f"{name} が未設定です。.env に記入してください（テンプレート: .env.example）。\n"
            f"想定パス: {get_env_path()}"
        )
    return text


def load_settings(
    *,
    app_dir: Path | None = None,
    require_api_key: bool = True,
    env_file: Path | None = None,
) -> Settings:
    """アプリ同階層の `.env` を読み設定を返す.

    cwd に依存しない。`require_api_key=False` のときはキー欠落を許す（診断用）。
    """
    base = app_dir if app_dir is not None else get_app_dir()
    env_path = env_file if env_file is not None else get_env_path(base)

    if not env_path.is_file():
        raise ConfigError(
            "`.env` が見つかりません。`.env.example` をコピーして `.env` を作成し、"
            "ご自身で取得した Gemini API キーを記入してください。\n"
            f"想定パス: {env_path}"
        )

    load_dotenv(dotenv_path=env_path, override=True)

    api_key = os.getenv("GEMINI_API_KEY", "")
    if require_api_key:
        api_key = _require_non_empty("GEMINI_API_KEY", api_key)
    else:
        api_key = (api_key or "").strip()

    try:
        cdp_port = int(os.getenv("CDP_PORT", "9222"))
    except ValueError as exc:
        raise ConfigError("CDP_PORT は整数で指定してください。") from exc

    user_data_raw = os.getenv("USER_DATA_DIR", "user-data").strip() or "user-data"
    chrome_path_raw = os.getenv("CHROME_PATH", "").strip() or None
    keep_raw = os.getenv("KEEP_BROWSER_OPEN", "true").strip().lower()
    keep_browser_open = keep_raw in {"1", "true", "yes", "on"}

    try:
        observe_max = int(os.getenv("OBSERVE_MAX_CANDIDATES", "40"))
    except ValueError as exc:
        raise ConfigError("OBSERVE_MAX_CANDIDATES は整数で指定してください。") from exc
    if observe_max < 1:
        raise ConfigError("OBSERVE_MAX_CANDIDATES は 1 以上にしてください。")

    try:
        max_steps = int(os.getenv("MAX_STEPS", "30"))
    except ValueError as exc:
        raise ConfigError("MAX_STEPS は整数で指定してください。") from exc
    if max_steps < 1:
        raise ConfigError("MAX_STEPS は 1 以上にしてください。")

    try:
        post_wait = int(os.getenv("POST_ACTION_WAIT_MS", "500"))
    except ValueError as exc:
        raise ConfigError("POST_ACTION_WAIT_MS は整数で指定してください。") from exc
    if post_wait < 0:
        raise ConfigError("POST_ACTION_WAIT_MS は 0 以上にしてください。")

    try:
        max_fail = int(os.getenv("MAX_CONSECUTIVE_FAILURES", "2"))
    except ValueError as exc:
        raise ConfigError("MAX_CONSECUTIVE_FAILURES は整数で指定してください。") from exc
    if max_fail < 1:
        raise ConfigError("MAX_CONSECUTIVE_FAILURES は 1 以上にしてください。")

    replan_raw = os.getenv("REPLAN_ON_FAILURE", "true").strip().lower()
    replan_on_failure = replan_raw in {"1", "true", "yes", "on"}

    collect_raw = os.getenv("COLLECT_MODE", "false").strip().lower()
    collect_mode = collect_raw in {"1", "true", "yes", "on"}

    return Settings(
        gemini_api_key=api_key,
        gemini_model_flash=os.getenv("GEMINI_MODEL_FLASH", "gemini-3.5-flash").strip()
        or "gemini-3.5-flash",
        gemini_model_flash_lite=os.getenv(
            "GEMINI_MODEL_FLASH_LITE", "gemini-3.5-flash-lite"
        ).strip()
        or "gemini-3.5-flash-lite",
        cdp_host=os.getenv("CDP_HOST", "127.0.0.1").strip() or "127.0.0.1",
        cdp_port=cdp_port,
        user_data_dir=resolve_under_app(user_data_raw, base),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        app_dir=base,
        env_path=env_path,
        chrome_path=chrome_path_raw,
        keep_browser_open=keep_browser_open,
        observe_max_candidates=observe_max,
        max_steps=max_steps,
        post_action_wait_ms=post_wait,
        max_consecutive_failures=max_fail,
        replan_on_failure=replan_on_failure,
        collect_mode=collect_mode,
    )
