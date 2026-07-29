"""Phase 3: 観察層のテスト."""

from __future__ import annotations

from pathlib import Path

import pytest

from browser_assistant.observe import (
    SCHEMA_VERSION,
    Observation,
    looks_like_full_html,
    observe_page,
    snapshots_differ,
    take_snapshot,
)

SAMPLE_HTML = """
<!doctype html>
<html>
<head><title>Sample Observe Page</title></head>
<body>
  <h1>Demo</h1>
  <a href="/about">About Link</a>
  <button type="button">検索する</button>
  <input type="text" name="q" placeholder="キーワード" />
  <textarea name="note" placeholder="メモ"></textarea>
  <select name="color"><option>red</option></select>
  <div>not interactive</div>
</body>
</html>
"""

MANY_BUTTONS_HTML = """
<!doctype html>
<html><head><title>Many</title></head><body>
""" + "\n".join(
    f'<button type="button">Btn{i}</button>' for i in range(80)
) + """
</body></html>
"""

PAGE2_HTML = """
<!doctype html>
<html><head><title>Page Two</title></head>
<body><button>OnlyOnPage2</button><a href="/x">X</a></body></html>
"""


@pytest.fixture(scope="module")
def browser_page():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        # CDP なしでも観察ロジックを検証できるよう Playwright 同梱 Chromium を使用
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Playwright Chromium を起動できません: {exc}")
        page = browser.new_page()
        try:
            yield page
        finally:
            browser.close()


def test_observe_sample_page_includes_controls(browser_page) -> None:
    browser_page.set_content(SAMPLE_HTML)
    obs = observe_page(browser_page, max_candidates=40)

    assert isinstance(obs, Observation)
    assert obs.schema_version == SCHEMA_VERSION
    assert obs.title == "Sample Observe Page"
    assert obs.candidate_count >= 4
    assert "candidates" in obs.to_dict()
    assert "snapshot" in obs.to_dict()

    tags = {c.tag for c in obs.candidates}
    names = " ".join((c.name or "") + " " + (c.text or "") for c in obs.candidates)
    assert "button" in tags
    assert "a" in tags
    assert "input" in tags
    assert "検索" in names or "キーワード" in names
    assert not looks_like_full_html(obs.to_dict())
    assert "html" not in obs.to_dict()
    assert "raw_html" not in obs.to_dict()


def test_observe_respects_max_candidates(browser_page) -> None:
    browser_page.set_content(MANY_BUTTONS_HTML)
    obs = observe_page(browser_page, max_candidates=10)
    assert obs.candidate_count == 10
    assert obs.truncated is True
    assert obs.max_candidates == 10


def test_observe_updates_after_navigation(browser_page) -> None:
    browser_page.set_content(SAMPLE_HTML)
    first = observe_page(browser_page)
    assert any("検索" in ((c.name or "") + (c.text or "")) for c in first.candidates)

    browser_page.set_content(PAGE2_HTML)
    second = observe_page(browser_page)
    assert second.title == "Page Two"
    joined = " ".join((c.name or "") + (c.text or "") for c in second.candidates)
    assert "OnlyOnPage2" in joined
    assert not any("検索する" in ((c.name or "") + (c.text or "")) for c in second.candidates)


def test_snapshots_differ_before_after(browser_page) -> None:
    browser_page.set_content(SAMPLE_HTML)
    before = take_snapshot(browser_page)
    browser_page.set_content(PAGE2_HTML)
    after = take_snapshot(browser_page)
    assert snapshots_differ(before, after)
    assert after.title == "Page Two"


def test_looks_like_full_html_detects_raw_key() -> None:
    assert looks_like_full_html({"html": "<html>" + ("x" * 6000)}) is True
    assert looks_like_full_html({"schema_version": "1", "candidates": []}) is False
