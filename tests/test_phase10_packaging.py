"""Phase 10: 起動チェック / 配布まわりのテスト."""

from __future__ import annotations

from pathlib import Path

from browser_assistant.startup_check import (
    ensure_env_example,
    format_startup_report,
    run_startup_checks,
    startup_ok,
)


def test_missing_env_is_reported_not_crash(tmp_path: Path) -> None:
    items = run_startup_checks(app_dir=tmp_path, require_api_key=True, check_chrome=False)
    assert any(i.code == "missing_env" and not i.ok for i in items)
    report = format_startup_report(items)
    assert "NG" in report
    assert not startup_ok(items)


def test_empty_api_key_reported(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("GEMINI_API_KEY=\nCDP_PORT=9222\n", encoding="utf-8")
    items = run_startup_checks(app_dir=tmp_path, require_api_key=True, check_chrome=False)
    assert any(i.code in {"empty_api_key", "config_error"} and not i.ok for i in items)


def test_valid_env_passes_key_check(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "GEMINI_API_KEY=test-key-xxxx\nCDP_PORT=9222\n",
        encoding="utf-8",
    )
    items = run_startup_checks(app_dir=tmp_path, require_api_key=True, check_chrome=False)
    assert any(i.code == "api_key" and i.ok for i in items)


def test_ensure_env_example_copies_template(tmp_path: Path) -> None:
    # リポジトリの .env.example からコピーされること
    dest = ensure_env_example(tmp_path)
    assert dest is not None
    assert dest.is_file()
    text = dest.read_text(encoding="utf-8")
    assert "GEMINI_API_KEY" in text
    assert "AIza" not in text  # 実キーっぽいものを含めない


def test_spec_does_not_bundle_dotenv_secret() -> None:
    spec = Path("packaging/browser_assistant.spec").read_text(encoding="utf-8")
    assert ".env.example" in spec
    assert "実キーの .env は絶対に同梱しない" in spec
    # datas に素の .env を追加していない（.env.example のみ）
    compact = spec.replace(" ", "")
    assert ".env.example" in spec
    assert "datas=[('.env'," not in compact
    assert 'datas=[(".env",' not in compact
