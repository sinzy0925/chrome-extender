"""Phase 5: 実行エンジンのテスト."""

from __future__ import annotations

import logging

import pytest

from browser_assistant.executor import (
    ActionError,
    ActionExecutor,
    ConfirmedAction,
    dispatch_action,
)

FORM_HTML = """
<!doctype html>
<html><head><title>Form</title></head>
<body>
  <input id="q" type="text" />
  <button id="go" type="button">Go</button>
  <div id="out"></div>
  <script>
    document.getElementById('go').addEventListener('click', () => {
      document.getElementById('out').textContent = document.getElementById('q').value || 'clicked';
    });
  </script>
</body></html>
"""


@pytest.fixture(scope="module")
def page():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chromium 起動不可: {exc}")
        pg = browser.new_page()
        try:
            yield pg
        finally:
            browser.close()


def test_goto_opens_url(page) -> None:
    executor = ActionExecutor(max_steps=5, post_wait_ms=0, reobserve=False)
    result = executor.run_one(
        page,
        ConfirmedAction(action="goto", url="https://example.com"),
    )
    assert result.ok
    assert "example.com" in page.url
    assert executor.steps_executed == 1


def test_click_and_type_with_known_selectors(page) -> None:
    page.set_content(FORM_HTML)
    executor = ActionExecutor(max_steps=10, post_wait_ms=0, reobserve=False)

    typed = executor.run_one(
        page,
        ConfirmedAction(action="type", selector="#q", text="hello"),
    )
    assert typed.ok
    assert page.input_value("#q") == "hello"

    clicked = executor.run_one(
        page,
        ConfirmedAction(action="click", selector="#go"),
    )
    assert clicked.ok
    assert page.locator("#out").inner_text() == "hello"
    assert executor.steps_executed == 2


def test_missing_element_stops_with_reason(page, caplog: pytest.LogCaptureFixture) -> None:
    page.set_content(FORM_HTML)
    executor = ActionExecutor(max_steps=5, post_wait_ms=0, reobserve=False)
    with caplog.at_level(logging.INFO, logger="browser_assistant.executor"):
        with pytest.raises(ActionError, match="見つかりません"):
            executor.run_one(
                page,
                ConfirmedAction(action="click", selector="#does-not-exist"),
            )
    # 失敗時はカウントしない（打ち切り用の成功手数として）
    assert executor.steps_executed == 0
    assert "実行(1手)" in caplog.text


def test_dispatch_runs_only_one_action(page) -> None:
    """1回の dispatch / run_one が複数手を実行しないこと."""
    page.set_content(FORM_HTML)
    # type だけ渡す。click は実行されない
    result = dispatch_action(
        page,
        ConfirmedAction(action="type", selector="#q", text="only-type"),
    )
    assert result.ok
    assert page.input_value("#q") == "only-type"
    assert page.locator("#out").inner_text() == ""


def test_max_steps_exceeded(page) -> None:
    page.set_content(FORM_HTML)
    executor = ActionExecutor(max_steps=1, post_wait_ms=0, reobserve=False)
    executor.run_one(page, ConfirmedAction(action="click", selector="#go"))
    with pytest.raises(ActionError, match="最大ステップ数"):
        executor.run_one(page, ConfirmedAction(action="click", selector="#go"))


def test_reobserve_hook_after_action(page) -> None:
    page.set_content(FORM_HTML)
    executor = ActionExecutor(max_steps=3, post_wait_ms=0, reobserve=True)
    result = executor.run_one(
        page,
        ConfirmedAction(action="type", selector="#q", text="x"),
    )
    assert result.snapshot is not None
    assert result.observation is not None
    assert result.observation.candidate_count >= 1
