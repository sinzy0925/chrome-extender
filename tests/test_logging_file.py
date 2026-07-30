"""ログファイル出力（固定名・UTF-8）の確認."""

from __future__ import annotations

import logging
from pathlib import Path

from browser_assistant.logging_setup import setup_logging
from browser_assistant.paths import get_default_log_path


def test_default_log_path_is_fixed_name(tmp_path: Path) -> None:
    assert get_default_log_path(tmp_path) == tmp_path / "log" / "browser_assistant.log"


def test_setup_logging_writes_utf8_japanese(tmp_path: Path) -> None:
    # モジュール状態をプロセス内テストでリセット
    import browser_assistant.logging_setup as ls

    ls._FILE_SESSION_STARTED = False

    path = get_default_log_path(tmp_path)
    setup_logging("INFO", app_dir=tmp_path)
    logging.getLogger("browser_assistant").info("既存のデバッグ Chrome を再利用します")

    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    text = path.read_text(encoding="utf-8-sig")
    assert "既存のデバッグ Chrome を再利用します" in text
    assert "browser_assistant" in text


def test_log_launch_banner_records_how_started(tmp_path: Path) -> None:
    import argparse

    import browser_assistant.logging_setup as ls
    from browser_assistant.logging_setup import log_launch_banner

    ls._FILE_SESSION_STARTED = False
    path = get_default_log_path(tmp_path)
    logger = setup_logging("INFO", app_dir=tmp_path)

    args = argparse.Namespace(
        ui=False,
        run="googleで検索して",
        collect_mode=True,
        close_browser=False,
        yes_danger=False,
        export_json="",
        export_csv="",
        startup_check=False,
        check_config=False,
        start_browser=False,
        observe=False,
        plan="",
        resolve_element="",
        execute_json="",
    )
    log_launch_banner(
        logger,
        argv=[
            "python",
            "-m",
            "browser_assistant",
            "--run",
            "googleで検索して",
            "--collect-mode",
        ],
        args=args,
        app_dir=tmp_path,
        version="0.0-test",
    )

    text = path.read_text(encoding="utf-8-sig")
    assert "===== 起動 =====" in text
    assert "command:" in text
    assert "--collect-mode" in text
    assert "instruction: googleで検索して" in text
    assert "mode: --run --collect-mode" in text
    assert "collect_mode: True" in text
    assert "version: 0.0-test" in text
