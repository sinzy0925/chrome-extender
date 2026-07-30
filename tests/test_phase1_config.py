"""Phase 1: 設定・パス・ログのテスト."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from browser_assistant.config import ConfigError, load_settings
from browser_assistant.logging_setup import setup_logging
from browser_assistant.paths import get_env_path, resolve_under_app


def _write_env(path: Path, **values: str) -> None:
    lines = [f"{k}={v}" for k, v in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_load_settings_reads_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    _write_env(
        env,
        GEMINI_API_KEY="test-key-1234",
        GEMINI_MODEL_FLASH="gemini-flash-test",
        GEMINI_MODEL_FLASH_LITE="gemini-lite-test",
        CDP_HOST="127.0.0.1",
        CDP_PORT="9333",
        USER_DATA_DIR="user-data",
        LOG_LEVEL="DEBUG",
    )
    # 環境に残っている値の影響を避ける
    for key in (
        "GEMINI_API_KEY",
        "GEMINI_MODEL_FLASH",
        "GEMINI_MODEL_FLASH_LITE",
        "CDP_HOST",
        "CDP_PORT",
        "USER_DATA_DIR",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = load_settings(app_dir=tmp_path)
    assert settings.gemini_api_key == "test-key-1234"
    assert settings.gemini_model_flash == "gemini-flash-test"
    assert settings.gemini_model_flash_lite == "gemini-lite-test"
    assert settings.cdp_port == 9333
    assert settings.log_level == "DEBUG"
    assert settings.user_data_dir == (tmp_path / "user-data").resolve()
    assert settings.env_path == env


def test_missing_env_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"\.env"):
        load_settings(app_dir=tmp_path)


def test_empty_api_key_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_env(tmp_path / ".env", GEMINI_API_KEY="")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        load_settings(app_dir=tmp_path)


def test_env_resolved_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_env(tmp_path / ".env", GEMINI_API_KEY="cwd-independent-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)

    settings = load_settings(app_dir=tmp_path)
    assert settings.gemini_api_key == "cwd-independent-key"
    assert get_env_path(tmp_path) == tmp_path / ".env"
    assert Path.cwd() == other


def test_log_level_changes_output_visibility(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("ERROR", log_file=False)
    log = logging.getLogger("browser_assistant.test")
    log.info("info-should-hide")
    log.error("error-should-show")
    err_out = capsys.readouterr().out
    assert "error-should-show" in err_out
    assert "info-should-hide" not in err_out

    setup_logging("INFO", log_file=False)
    log.info("info-should-show")
    out = capsys.readouterr().out
    assert "info-should-show" in out


def test_resolve_under_app_relative(tmp_path: Path) -> None:
    assert resolve_under_app("user-data", tmp_path) == (tmp_path / "user-data").resolve()


def test_google_genai_is_importable() -> None:
    import google.genai
    import importlib.metadata

    version = importlib.metadata.version("google-genai")
    # 仕様: 最新系の公式 SDK（2.x）。レガシー google-generativeai ではないこと。
    assert version.split(".")[0] == "2"
    assert hasattr(google.genai, "Client")
