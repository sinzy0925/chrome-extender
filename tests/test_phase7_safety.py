"""Phase 7: 安全ガードのテスト."""

from __future__ import annotations

import pytest

from browser_assistant.agent import AgentLoop
from browser_assistant.executor import ActionExecutor, ConfirmedAction
from browser_assistant.safety import (
    ConfirmDecision,
    assess_action,
    match_dangerous_keywords,
)
from browser_assistant.schemas import ElementResolution, PlanStep, StepPlan


class FakeGemini:
    def __init__(self, plan: StepPlan, resolution: ElementResolution) -> None:
        self.plan = plan
        self.resolution = resolution
        self.plan_calls = 0

    def plan_steps(self, instruction: str, *, current_url: str | None = None) -> StepPlan:
        self.plan_calls += 1
        return self.plan

    def resolve_element(self, step_instruction, observation, *, preferred_action=None):
        return self.resolution


FORM_HTML = """
<!doctype html>
<html><head><title>Safety</title></head>
<body>
  <button id="go">Go</button>
  <button id="buy">購入する</button>
  <div id="out"></div>
  <script>
    document.getElementById('go').onclick = () => {
      document.getElementById('out').textContent = 'safe';
    };
    document.getElementById('buy').onclick = () => {
      document.getElementById('out').textContent = 'bought';
    };
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


def test_dangerous_keywords_detected() -> None:
    hits = match_dangerous_keywords("このアカウントを削除して購入を確定")
    assert "削除" in hits
    assert "購入" in hits


def test_risk_high_needs_confirmation() -> None:
    verdict = assess_action(
        ConfirmedAction(action="click", risk="high", selector="#x", reason="ok")
    )
    assert verdict.needs_confirmation
    assert "risk=high" in verdict.summary


def test_safe_goto_does_not_need_confirmation() -> None:
    verdict = assess_action(
        ConfirmedAction(
            action="goto",
            risk="low",
            url="https://example.com",
            reason="open",
        )
    )
    assert not verdict.needs_confirmation


def test_danger_not_executed_without_callback(page) -> None:
    page.set_content(FORM_HTML)
    plan = StepPlan(
        steps=[
            PlanStep(
                id="1",
                instruction="購入する",
                action="click",
                risk="high",
                reason="購入ボタン",
            ),
            PlanStep(id="2", instruction="done", action="done", risk="low"),
        ]
    )
    gemini = FakeGemini(
        plan,
        ElementResolution(
            candidate_id="e1",
            action="click",
            risk="high",
            reason="購入する",
            selector_hint="#buy",
        ),
    )
    loop = AgentLoop(
        gemini=gemini,  # type: ignore[arg-type]
        executor=ActionExecutor(max_steps=5, post_wait_ms=0, reobserve=False),
        replan_on_failure=False,
        confirm_callback=None,
    )
    result = loop.run(page, "購入して")
    assert result.status == "stopped"
    assert page.locator("#out").inner_text() == ""


def test_confirm_execute_allows_one_action(page) -> None:
    page.set_content(FORM_HTML)
    plan = StepPlan(
        steps=[
            PlanStep(id="1", instruction="購入", action="click", risk="high", reason="購入"),
            PlanStep(id="2", instruction="done", action="done", risk="low"),
        ]
    )
    gemini = FakeGemini(
        plan,
        ElementResolution(
            candidate_id="e1",
            action="click",
            risk="high",
            reason="購入する",
            selector_hint="#buy",
        ),
    )
    decisions = {"n": 0}

    def _cb(action, verdict):
        decisions["n"] += 1
        return ConfirmDecision.EXECUTE

    loop = AgentLoop(
        gemini=gemini,  # type: ignore[arg-type]
        executor=ActionExecutor(max_steps=5, post_wait_ms=0, reobserve=False),
        replan_on_failure=False,
        confirm_callback=_cb,
    )
    result = loop.run(page, "購入")
    assert result.status == "completed"
    assert decisions["n"] == 1
    assert page.locator("#out").inner_text() == "bought"


def test_confirm_skip_and_abort(page) -> None:
    page.set_content(FORM_HTML)
    plan = StepPlan(
        steps=[
            PlanStep(id="1", instruction="削除", action="click", risk="high", reason="削除"),
            PlanStep(id="2", instruction="Go", action="click", risk="low", reason="safe"),
            PlanStep(id="3", instruction="done", action="done", risk="low"),
        ]
    )

    class SeqGemini(FakeGemini):
        def resolve_element(self, step_instruction, observation, *, preferred_action=None):
            if "削除" in step_instruction:
                return ElementResolution(
                    candidate_id="e1",
                    action="click",
                    risk="high",
                    reason="削除",
                    selector_hint="#buy",
                )
            return ElementResolution(
                candidate_id="e2",
                action="click",
                risk="low",
                reason="go",
                selector_hint="#go",
            )

    gemini = SeqGemini(plan, ElementResolution("e1", "click", "high", "x", selector_hint="#buy"))
    decisions = iter([ConfirmDecision.SKIP])

    def _cb(action, verdict):
        return next(decisions)

    loop = AgentLoop(
        gemini=gemini,  # type: ignore[arg-type]
        executor=ActionExecutor(max_steps=5, post_wait_ms=0, reobserve=False),
        replan_on_failure=False,
        confirm_callback=_cb,
    )
    result = loop.run(page, "削除してからGo")
    assert result.status == "completed"
    assert page.locator("#out").inner_text() == "safe"

    # abort
    page.set_content(FORM_HTML)
    gemini2 = FakeGemini(
        StepPlan(
            steps=[
                PlanStep(id="1", instruction="送信", action="click", risk="high", reason="送信"),
            ]
        ),
        ElementResolution(
            candidate_id="e1",
            action="click",
            risk="high",
            reason="送信",
            selector_hint="#buy",
        ),
    )
    loop2 = AgentLoop(
        gemini=gemini2,  # type: ignore[arg-type]
        executor=ActionExecutor(max_steps=5, post_wait_ms=0, reobserve=False),
        replan_on_failure=False,
        confirm_callback=lambda a, v: ConfirmDecision.ABORT,
    )
    result2 = loop2.run(page, "送信")
    assert result2.status == "stopped"
    assert page.locator("#out").inner_text() == ""


def test_safe_actions_auto_run(page) -> None:
    page.set_content(FORM_HTML)
    plan = StepPlan(
        steps=[
            PlanStep(id="1", instruction="Go", action="click", risk="low", reason="go"),
            PlanStep(id="2", instruction="done", action="done", risk="low"),
        ]
    )
    gemini = FakeGemini(
        plan,
        ElementResolution(
            candidate_id="e1",
            action="click",
            risk="low",
            reason="Go",
            selector_hint="#go",
        ),
    )
    called = {"n": 0}

    def _cb(action, verdict):
        called["n"] += 1
        return ConfirmDecision.EXECUTE

    loop = AgentLoop(
        gemini=gemini,  # type: ignore[arg-type]
        executor=ActionExecutor(max_steps=5, post_wait_ms=0, reobserve=False),
        replan_on_failure=False,
        confirm_callback=_cb,
    )
    result = loop.run(page, "Goを押す")
    assert result.status == "completed"
    assert called["n"] == 0  # 確認不要
    assert page.locator("#out").inner_text() == "safe"


def test_collect_mode_blocks_writes(page) -> None:
    page.set_content(FORM_HTML)
    plan = StepPlan(
        steps=[
            PlanStep(id="1", instruction="Go", action="click", risk="low", reason="go"),
            PlanStep(id="2", instruction="done", action="done", risk="low"),
        ]
    )
    gemini = FakeGemini(
        plan,
        ElementResolution(
            candidate_id="e1",
            action="click",
            risk="low",
            reason="Go",
            selector_hint="#go",
        ),
    )
    loop = AgentLoop(
        gemini=gemini,  # type: ignore[arg-type]
        executor=ActionExecutor(max_steps=5, post_wait_ms=0, reobserve=False),
        replan_on_failure=False,
        collect_mode=True,
    )
    result = loop.run(page, "クリック")
    assert result.status == "completed"
    assert page.locator("#out").inner_text() == ""  # click ブロック
    assert any(e.phase == "safety" for e in result.events)
