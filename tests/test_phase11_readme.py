"""Phase 11: README / 受け入れ文書の静的確認."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_has_product_definition_and_disclaimer() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for needle in (
        "製品定義",
        "事前ログイン",
        "自己責任",
        "CAPTCHA",
        "保証範囲",
        "対象外",
        "初回の使い方",
        "GEMINI_API_KEY",
        "docs/DISCLAIMER.md",
        "docs/DISTRIBUTION.md",
    ):
        assert needle in text, f"missing: {needle}"
    assert "突破します" not in text
    assert text.count("APIキーはアプリ") == 1  # 重複案内がないこと


def test_readme_usage_order_covers_env_login_run() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    # 手順に .env → ログイン → 指示 の流れがある
    assert ".env" in text
    assert "事前ログイン" in text
    assert "--ui" in text or "--run" in text
    assert "Stop" in text or "Ctrl+C" in text


def test_spec_phase11_and_acceptance_checked() -> None:
    text = (ROOT / "docs" / "MVP_SPEC.md").read_text(encoding="utf-8")
    assert "### Phase 11" in text
    # Phase 11 実装・テストが完了マーク
    section = text.split("### Phase 11")[1].split("## 5.")[0]
    assert "- [ ]" not in section
    acceptance = text.split("## 5. MVP受け入れ基準")[1].split("## 6.")[0]
    assert "- [ ]" not in acceptance


def test_out_of_scope_not_oversold() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    disc = (ROOT / "docs" / "DISCLAIMER.md").read_text(encoding="utf-8")
    for text in (readme, disc):
        assert "CAPTCHA" in text
        assert "突破はしません" in text or "突破は行いません" in text
