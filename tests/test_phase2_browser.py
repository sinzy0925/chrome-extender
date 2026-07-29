"""Phase 2: CDP / Chrome 起動まわりのテスト."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from browser_assistant.browser import (
    LOGIN_GUIDE,
    BrowserError,
    BrowserSession,
    find_chrome_executable,
    is_cdp_available,
    is_port_open,
)
from browser_assistant.config import Settings


def _settings(tmp_path: Path, port: int = 19222) -> Settings:
    return Settings(
        gemini_api_key="test",
        gemini_model_flash="flash",
        gemini_model_flash_lite="lite",
        cdp_host="127.0.0.1",
        cdp_port=port,
        user_data_dir=tmp_path / "user-data",
        log_level="INFO",
        app_dir=tmp_path,
        env_path=tmp_path / ".env",
        chrome_path=None,
        keep_browser_open=True,
        observe_max_candidates=40,
        max_steps=30,
        post_action_wait_ms=500,
    )


def test_find_chrome_executable_explicit(tmp_path: Path) -> None:
    chrome = tmp_path / "chrome.exe"
    chrome.write_bytes(b"")
    assert find_chrome_executable(chrome) == chrome.resolve()
    assert find_chrome_executable(tmp_path / "missing.exe") is None


def test_is_cdp_available_false_when_nothing_listens() -> None:
    assert is_cdp_available("127.0.0.1", 1, timeout=0.2) is False


def test_port_busy_but_not_cdp_raises(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    session = BrowserSession(settings=settings, keep_open=True)

    with (
        patch("browser_assistant.browser.is_cdp_available", return_value=False),
        patch("browser_assistant.browser.is_port_open", return_value=True),
        pytest.raises(BrowserError, match="CDP"),
    ):
        session.start()


def test_chrome_missing_raises_friendly_message(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    session = BrowserSession(settings=settings, chrome_path=None, keep_open=True)

    with (
        patch("browser_assistant.browser.is_cdp_available", return_value=False),
        patch("browser_assistant.browser.is_port_open", return_value=False),
        patch("browser_assistant.browser.find_chrome_executable", return_value=None),
        pytest.raises(BrowserError, match="Chrome"),
    ):
        session.start()


def test_reuse_existing_cdp_and_log_login_guide(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path)
    session = BrowserSession(settings=settings, keep_open=True)

    mock_browser = MagicMock()
    mock_browser.contexts = []
    mock_pw = MagicMock()
    mock_pw.chromium.connect_over_cdp.return_value = mock_browser

    mock_cm = MagicMock()
    mock_cm.start.return_value = mock_pw

    with (
        patch("browser_assistant.browser.is_cdp_available", return_value=True),
        patch("playwright.sync_api.sync_playwright", return_value=mock_cm),
        caplog.at_level(logging.INFO, logger="browser_assistant.browser"),
    ):
        session.start()
        session.stop()

    assert LOGIN_GUIDE in caplog.text
    assert "再利用" in caplog.text
    assert "ブラウザは起動したまま残します" in caplog.text
    mock_browser.close.assert_not_called()
    mock_pw.stop.assert_called()


def test_close_browser_calls_browser_close(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    session = BrowserSession(settings=settings, keep_open=False)

    mock_browser = MagicMock()
    mock_browser.contexts = []
    mock_pw = MagicMock()
    mock_pw.chromium.connect_over_cdp.return_value = mock_browser
    mock_cm = MagicMock()
    mock_cm.start.return_value = mock_pw

    with (
        patch("browser_assistant.browser.is_cdp_available", return_value=True),
        patch("playwright.sync_api.sync_playwright", return_value=mock_cm),
    ):
        session.start()
        session.stop()

    mock_browser.close.assert_called_once()
    mock_pw.stop.assert_called()


@pytest.mark.integration
def test_real_chrome_cdp_connect(tmp_path: Path) -> None:
    """実 Chrome がある環境での結合テスト。無ければ skip。"""
    chrome = find_chrome_executable()
    if chrome is None:
        pytest.skip("Chrome がインストールされていません")

    # 衝突しにくいポート
    port = 19233
    if is_cdp_available("127.0.0.1", port) or is_port_open("127.0.0.1", port):
        pytest.skip(f"ポート {port} が既に使用中です")

    settings = _settings(tmp_path, port=port)
    session = BrowserSession(
        settings=settings,
        keep_open=False,
        chrome_path=chrome,
    )
    try:
        session.start()
        assert session.browser is not None
        assert is_cdp_available("127.0.0.1", port)
        # 固定 user-data-dir が使われ、プロファイル領域が作られること
        assert settings.user_data_dir.exists()
        assert any(settings.user_data_dir.iterdir())
    finally:
        session.stop()
        # 閉じたあと CDP が落ちていること（多少の遅延許容）
        import time

        time.sleep(1.5)
        assert not is_cdp_available("127.0.0.1", port, timeout=0.5)
