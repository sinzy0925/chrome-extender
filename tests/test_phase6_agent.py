"""Phase 6: エージェントループのテスト."""

from __future__ import annotations

import logging

import pytest

from browser_assistant.agent import AgentLoop
from browser_assistant.executor import ActionExecutor
from browser_assistant.schemas import ElementResolution, PlanStep, StepPlan


class FakeGemini:
    def __init__(
        self,
        plan: StepPlan,
        resolution: ElementResolution | None = None,
    ) -> None:
        self.plan = plan
        self.resolution = resolution or ElementResolution(
            candidate_id="e1",
            action="click",
            risk="low",
            reason="ok",
            selector_hint="#go",
        )
        self.plan_calls = 0
        self.resolve_calls = 0
        self.replan: StepPlan | None = None
        self._resolve_seq: list[ElementResolution] | None = None

    def plan_steps(
        self, instruction: str, *, current_url: str | None = None, intent=None
    ) -> StepPlan:
        self.plan_calls += 1
        if self.plan_calls > 1 and self.replan is not None:
            return self.replan
        return self.plan

    def resolve_element(self, step_instruction, observation, *, preferred_action=None):
        self.resolve_calls += 1
        if self._resolve_seq is not None:
            return self._resolve_seq[min(self.resolve_calls - 1, len(self._resolve_seq) - 1)]
        return self.resolution


FORM_HTML = """
<!doctype html>
<html><head><title>Agent Form</title></head>
<body>
  <input id="q" type="text" />
  <button id="go" type="button">Go</button>
  <div id="out"></div>
  <script>
    document.getElementById('go').addEventListener('click', () => {
      document.getElementById('out').textContent =
        document.getElementById('q').value || 'clicked';
    });
  </script>
</body></html>
"""


@pytest.fixture
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


def test_agent_runs_plan_observe_resolve_execute(page, caplog: pytest.LogCaptureFixture) -> None:
    page.set_content(FORM_HTML)
    plan = StepPlan(
        steps=[
            PlanStep(
                id="1",
                instruction="入力欄に hello と入れる",
                action="type",
                risk="low",
                text="hello",
            ),
            PlanStep(
                id="2",
                instruction="Go を押す",
                action="click",
                risk="low",
            ),
            PlanStep(id="3", instruction="完了", action="done", risk="low"),
        ]
    )

    gemini = FakeGemini(plan)
    gemini._resolve_seq = [
        ElementResolution(
            candidate_id="e1",
            action="type",
            risk="low",
            reason="input",
            selector_hint="#q",
            text="hello",
        ),
        ElementResolution(
            candidate_id="e2",
            action="click",
            risk="low",
            reason="button",
            selector_hint="#go",
        ),
    ]
    executor = ActionExecutor(max_steps=10, post_wait_ms=0, reobserve=False)
    events: list[str] = []

    loop = AgentLoop(
        gemini=gemini,  # type: ignore[arg-type]
        executor=executor,
        replan_on_failure=False,
        on_event=lambda e: events.append(e.phase),
    )

    with caplog.at_level(logging.INFO, logger="browser_assistant.agent"):
        result = loop.run(page, "フォームに入力して送信風にクリック")

    assert result.status == "completed"
    assert page.locator("#out").inner_text() == "hello"
    assert "plan" in events
    assert "observe" in events
    assert "resolve" in events
    assert "execute" in events


def test_stop_prevents_further_actions(page) -> None:
    page.set_content(FORM_HTML)
    plan = StepPlan(
        steps=[
            PlanStep(id="1", instruction="wait", action="wait", risk="low"),
            PlanStep(id="2", instruction="click", action="click", risk="low"),
            PlanStep(id="3", instruction="done", action="done", risk="low"),
        ]
    )
    gemini = FakeGemini(plan)
    executor = ActionExecutor(max_steps=10, post_wait_ms=0, reobserve=False)
    loop = AgentLoop(gemini=gemini, executor=executor, replan_on_failure=False)  # type: ignore[arg-type]
    loop.request_stop()
    result = loop.run(page, "何かする")
    assert result.status == "stopped"
    assert page.locator("#out").inner_text() == ""
    assert gemini.resolve_calls == 0


def test_ask_user_does_not_auto_continue(page) -> None:
    page.set_content(FORM_HTML)
    plan = StepPlan(
        steps=[
            PlanStep(
                id="1",
                instruction="確認が必要",
                action="ask_user",
                risk="high",
                reason="危険なので確認",
            ),
            PlanStep(id="2", instruction="click", action="click", risk="low"),
        ]
    )
    gemini = FakeGemini(plan)
    executor = ActionExecutor(max_steps=10, post_wait_ms=0, reobserve=False)
    loop = AgentLoop(gemini=gemini, executor=executor, replan_on_failure=False)  # type: ignore[arg-type]
    result = loop.run(page, "危険操作")
    assert result.status == "ask_user"
    assert "確認" in result.message
    assert page.locator("#out").inner_text() == ""


def test_failure_replans_once_then_can_complete(page) -> None:
    page.set_content(FORM_HTML)

    bad = StepPlan(
        steps=[
            PlanStep(id="1", instruction="存在しない", action="click", risk="low"),
        ]
    )
    good = StepPlan(
        steps=[
            PlanStep(id="1", instruction="Go", action="click", risk="low"),
            PlanStep(id="2", instruction="done", action="done", risk="low"),
        ]
    )
    gemini = FakeGemini(
        bad,
        resolution=ElementResolution(
            candidate_id="e9",
            action="click",
            risk="low",
            reason="bad",
            selector_hint="#missing",
        ),
    )
    gemini.replan = good
    gemini._resolve_seq = [
        ElementResolution(
            candidate_id="e9",
            action="click",
            risk="low",
            reason="bad",
            selector_hint="#missing",
        ),
        ElementResolution(
            candidate_id="e1",
            action="click",
            risk="low",
            reason="go",
            selector_hint="#go",
        ),
    ]
    executor = ActionExecutor(max_steps=10, post_wait_ms=0, reobserve=False)
    loop = AgentLoop(
        gemini=gemini,  # type: ignore[arg-type]
        executor=executor,
        replan_on_failure=True,
        max_consecutive_failures=2,
    )
    result = loop.run(page, "クリックして")
    assert result.status == "completed"
    assert gemini.plan_calls >= 2
    assert page.locator("#out").inner_text() == "clicked"


def test_failure_without_replan_stops(page) -> None:
    page.set_content(FORM_HTML)
    plan = StepPlan(
        steps=[
            PlanStep(id="1", instruction="missing", action="click", risk="low"),
        ]
    )
    gemini = FakeGemini(
        plan,
        resolution=ElementResolution(
            candidate_id="e9",
            action="click",
            risk="low",
            reason="bad",
            selector_hint="#missing",
        ),
    )
    executor = ActionExecutor(max_steps=5, post_wait_ms=0, reobserve=False)
    loop = AgentLoop(
        gemini=gemini,  # type: ignore[arg-type]
        executor=executor,
        replan_on_failure=False,
    )
    result = loop.run(page, "失敗する")
    assert result.status == "failed"
    assert gemini.plan_calls == 1
