"""エージェントループ: 分解 → 観察 → 確定 → 1手実行."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from browser_assistant.executor import ActionError, ActionExecutor, ActionResult, ConfirmedAction
from browser_assistant.gemini_client import GeminiClient, GeminiError
from browser_assistant.intent.normalize import (
    NormalizedIntent,
    intent_result_satisfied,
    normalize_intent,
)
from browser_assistant.observe import Observation, get_active_page, observe_page
from browser_assistant.safety import (
    ConfirmCallback,
    ConfirmDecision,
    SafetyVerdict,
    assess_action,
)
from browser_assistant.schemas import ElementResolution, PlanStep, StepPlan

logger = logging.getLogger("browser_assistant.agent")

DEFAULT_MAX_CONSECUTIVE_FAILURES = 2


@dataclass
class AgentEvent:
    phase: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"phase": self.phase, "message": self.message, "data": self.data}


@dataclass
class AgentRunResult:
    status: str  # completed | stopped | ask_user | failed
    message: str
    events: list[AgentEvent] = field(default_factory=list)
    extracts: list[dict[str, Any]] = field(default_factory=list)
    intent: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "events": [e.to_dict() for e in self.events],
            "extracts": self.extracts,
            "intent": self.intent,
        }


class AgentLoop:
    """日本語指示を分解し、1ステップずつ観察・確定・実行する."""

    def __init__(
        self,
        *,
        gemini: GeminiClient,
        executor: ActionExecutor,
        max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
        replan_on_failure: bool = True,
        observe_max_candidates: int = 40,
        on_event: Callable[[AgentEvent], None] | None = None,
        confirm_callback: ConfirmCallback | None = None,
        collect_mode: bool = False,
        app_dir: Path | None = None,
        intent_sites_path: Path | None = None,
        intent_phrases_path: Path | None = None,
        intent_default_result_type: str = "urls",
        extract_url_limit: int = 50,
    ) -> None:
        self.gemini = gemini
        self.executor = executor
        self.max_consecutive_failures = max(1, max_consecutive_failures)
        self.replan_on_failure = replan_on_failure
        self.observe_max_candidates = observe_max_candidates
        self.on_event = on_event
        self.confirm_callback = confirm_callback
        self.collect_mode = collect_mode
        self.app_dir = app_dir
        self.intent_sites_path = intent_sites_path
        self.intent_phrases_path = intent_phrases_path
        self.intent_default_result_type = intent_default_result_type
        self.extract_url_limit = extract_url_limit
        self._stop = threading.Event()
        self._replanned = False
        self._intent: NormalizedIntent | None = None

    def request_stop(self) -> None:
        """実行中のループを次のチェックポイントで止める."""
        self._stop.set()
        logger.info("Stop が要求されました")

    def clear_stop(self) -> None:
        self._stop.clear()

    def _apply_safety(
        self,
        confirmed: ConfirmedAction,
        safety: SafetyVerdict,
    ) -> str:
        """戻り値: allow | skip | abort | blocked."""
        if safety.blocked_by_collect_mode:
            return "blocked"
        if not safety.needs_confirmation:
            return "allow"
        self._emit(
            "safety",
            "危険操作のため確認が必要です",
            verdict=safety.summary,
            action=confirmed.to_dict(),
        )
        if self.confirm_callback is None:
            # 確認UIが無い場合は実行しない（安全側）
            return "abort"
        decision = self.confirm_callback(confirmed, safety)
        if decision == ConfirmDecision.EXECUTE:
            return "allow"
        if decision == ConfirmDecision.SKIP:
            return "skip"
        return "abort"

    def _emit(self, phase: str, message: str, **data: Any) -> None:
        event = AgentEvent(phase=phase, message=message, data=data)
        logger.info("[%s] %s %s", phase, message, data or "")
        if self.on_event:
            self.on_event(event)
        self._events.append(event)

    def _normalize(self, instruction: str) -> NormalizedIntent:
        from browser_assistant.intent.result_type import ResultType

        default_rt: ResultType = "urls"
        if self.intent_default_result_type in {"urls", "title", "text", "none"}:
            default_rt = self.intent_default_result_type  # type: ignore[assignment]
        return normalize_intent(
            instruction,
            app_dir=self.app_dir,
            sites_path=self.intent_sites_path,
            phrases_path=self.intent_phrases_path,
            default_result_type=default_rt,
        )

    def run(self, page: Any, instruction: str) -> AgentRunResult:
        self._replanned = False
        self._events: list[AgentEvent] = []
        extracts: list[dict[str, Any]] = []

        instruction = (instruction or "").strip()
        if not instruction:
            return AgentRunResult(status="failed", message="指示が空です", events=self._events)

        intent = self._normalize(instruction)
        self._intent = intent
        self._emit("intent", "指示を正規化しました", **intent.to_dict())

        try:
            if self._stop.is_set():
                self._emit("stop", "開始前に中断されました")
                return AgentRunResult(
                    status="stopped",
                    message="Stop により中断しました",
                    events=self._events,
                    extracts=extracts,
                    intent=intent.to_dict(),
                )
            self._emit(
                "plan",
                "手順分解を開始",
                instruction=instruction,
                normalized=intent.normalized_instruction,
            )
            plan = self.gemini.plan_steps(
                instruction,
                current_url=getattr(page, "url", None),
                intent=intent.to_dict(),
            )
            self._emit(
                "plan",
                "手順分解完了",
                steps=[s.to_dict() for s in plan.steps],
            )
        except GeminiError as exc:
            self._emit("plan", f"手順分解失敗: {exc}")
            return AgentRunResult(
                status="failed",
                message=str(exc),
                events=self._events,
                intent=intent.to_dict(),
            )

        steps = list(plan.steps)
        consecutive_failures = 0
        idx = 0

        while idx < len(steps):
            if self._stop.is_set():
                self._emit("stop", "ユーザーにより中断されました")
                return AgentRunResult(
                    status="stopped",
                    message="Stop により中断しました",
                    events=self._events,
                    extracts=extracts,
                    intent=intent.to_dict(),
                )

            step = steps[idx]
            self._emit(
                "step",
                f"ステップ開始 {idx + 1}/{len(steps)}",
                step=step.to_dict(),
            )

            if step.action == "done":
                if not intent_result_satisfied(intent, extracts):
                    self._emit(
                        "done",
                        "成果物が不足しているため完了を保留",
                        result_type=intent.result_type,
                        extracts=extracts,
                    )
                    if self.replan_on_failure and not self._replanned:
                        replan = self._try_replan(
                            page,
                            intent.normalized_instruction,
                            steps[idx:],
                            f"result_type={intent.result_type} の成果がまだ取れていません",
                            intent=intent,
                        )
                        if replan is not None and replan.steps:
                            self._replanned = True
                            consecutive_failures = 0
                            steps = list(replan.steps)
                            idx = 0
                            self._emit(
                                "replan",
                                "成果不足のため再計画しました（1回まで）",
                                steps=[s.to_dict() for s in replan.steps],
                            )
                            continue
                    return AgentRunResult(
                        status="failed",
                        message=(
                            f"完了条件未達: result_type={intent.result_type} の結果が取れていません"
                        ),
                        events=self._events,
                        extracts=extracts,
                        intent=intent.to_dict(),
                    )

                self._emit("done", "完了ステップに到達")
                return AgentRunResult(
                    status="completed",
                    message="完了しました",
                    events=self._events,
                    extracts=extracts,
                    intent=intent.to_dict(),
                )

            if step.action == "ask_user":
                self._emit("ask_user", step.reason or step.instruction, step=step.to_dict())
                return AgentRunResult(
                    status="ask_user",
                    message=step.reason or step.instruction or "ユーザー確認が必要です",
                    events=self._events,
                    extracts=extracts,
                    intent=intent.to_dict(),
                )

            try:
                confirmed = self._prepare_action(page, step, intent=intent)
                if confirmed.action == "ask_user":
                    self._emit("ask_user", confirmed.reason or "要素を確定できませんでした")
                    return AgentRunResult(
                        status="ask_user",
                        message=confirmed.reason or "ユーザー確認が必要です",
                        events=self._events,
                        extracts=extracts,
                        intent=intent.to_dict(),
                    )

                if self._stop.is_set():
                    self._emit("stop", "実行直前に中断されました")
                    return AgentRunResult(
                        status="stopped",
                        message="Stop により中断しました",
                        events=self._events,
                        extracts=extracts,
                        intent=intent.to_dict(),
                    )

                safety = assess_action(confirmed, collect_mode=self.collect_mode)
                gate = self._apply_safety(confirmed, safety)
                if gate == "abort":
                    self._emit("safety", "危険操作が中止されました", verdict=safety.summary)
                    return AgentRunResult(
                        status="stopped",
                        message=f"安全確認で中止: {safety.summary}",
                        events=self._events,
                        extracts=extracts,
                        intent=intent.to_dict(),
                    )
                if gate == "skip":
                    self._emit("safety", "この手をスキップしました", verdict=safety.summary)
                    consecutive_failures = 0
                    idx += 1
                    continue
                if gate == "blocked":
                    self._emit(
                        "safety",
                        "収集モードにより書き込み操作をブロック",
                        action=confirmed.to_dict(),
                    )
                    consecutive_failures = 0
                    idx += 1
                    continue

                self._emit("execute", "1手実行", action=confirmed.to_dict())
                result = self.executor.run_one(page, confirmed)
                # 大きな observation はログを膨らませるので要約だけイベントへ
                slim = result.to_dict()
                if "observation" in slim:
                    obs = slim["observation"]
                    slim["observation"] = {
                        "url": obs.get("url"),
                        "title": obs.get("title"),
                        "candidate_count": obs.get("candidate_count"),
                    }
                # URL一覧は formatted を前面に
                if result.action == "extract" and isinstance(result.data, dict):
                    data = result.data
                    if data.get("formatted"):
                        slim["data"] = {
                            "count": data.get("count"),
                            "formatted": data.get("formatted"),
                            "title": data.get("title"),
                            "page_url": data.get("page_url"),
                        }
                self._emit("execute", "1手成功", result=slim)
                if result.action == "extract" and result.data:
                    extracts.append(result.data)
                consecutive_failures = 0
                idx += 1
            except (ActionError, GeminiError) as exc:
                consecutive_failures += 1
                self._emit(
                    "error",
                    str(exc),
                    consecutive_failures=consecutive_failures,
                    step=step.to_dict(),
                )

                if (
                    self.replan_on_failure
                    and not self._replanned
                    and consecutive_failures >= 1
                ):
                    replan = self._try_replan(
                        page,
                        intent.normalized_instruction,
                        steps[idx:],
                        str(exc),
                        intent=intent,
                    )
                    if replan is not None and replan.steps:
                        self._replanned = True
                        consecutive_failures = 0
                        steps = list(replan.steps)
                        idx = 0
                        self._emit(
                            "replan",
                            "残り手順を再計画しました（1回まで）",
                            steps=[s.to_dict() for s in replan.steps],
                        )
                        continue

                if consecutive_failures >= self.max_consecutive_failures:
                    self._emit("failed", "連続失敗のため停止します")
                    return AgentRunResult(
                        status="failed",
                        message=f"連続失敗のため停止: {exc}",
                        events=self._events,
                        extracts=extracts,
                        intent=intent.to_dict(),
                    )

                # 再計画不可・閾値未満でも無限リトライはしない
                return AgentRunResult(
                    status="failed",
                    message=str(exc),
                    events=self._events,
                    extracts=extracts,
                    intent=intent.to_dict(),
                )

        if not intent_result_satisfied(intent, extracts):
            self._emit("done", "全ステップ消化したが成果不足", result_type=intent.result_type)
            return AgentRunResult(
                status="failed",
                message=f"成果不足: result_type={intent.result_type}",
                events=self._events,
                extracts=extracts,
                intent=intent.to_dict(),
            )

        self._emit("done", "全ステップ消化（done なし）")
        return AgentRunResult(
            status="completed",
            message="全ステップを実行しました",
            events=self._events,
            extracts=extracts,
            intent=intent.to_dict(),
        )

    def _try_replan(
        self,
        page: Any,
        original_instruction: str,
        remaining: list[PlanStep],
        error: str,
        *,
        intent: NormalizedIntent | None = None,
    ) -> StepPlan | None:
        try:
            prompt = (
                f"元の指示: {original_instruction}\n"
                f"直前のエラー: {error}\n"
                f"未完了だった手順: {[s.to_dict() for s in remaining]}\n"
                "現在のページ状態を踏まえ、残りをやり遂げるための新しい細かい手順に再分解してください。"
            )
            return self.gemini.plan_steps(
                prompt,
                current_url=getattr(page, "url", None),
                intent=intent.to_dict() if intent else None,
            )
        except GeminiError as exc:
            self._emit("replan", f"再計画失敗: {exc}")
            return None

    def _prepare_action(
        self,
        page: Any,
        step: PlanStep,
        *,
        intent: NormalizedIntent | None = None,
    ) -> ConfirmedAction:
        """ステップ種別に応じて観察・要素確定し、ConfirmedAction を作る."""
        if step.action == "goto":
            url = step.url or _extract_url_from_text(step.instruction)
            if not url and intent and intent.target_url:
                url = intent.target_url
            # Google + クエリなら検索結果へ直接行く（click 失敗を避ける）
            if intent and intent.query_hint and intent.site_alias == "google":
                from urllib.parse import quote

                q = quote(intent.query_hint)
                search_url = f"https://www.google.com/search?q={q}"
                if not url or (
                    "google." in url
                    and "/search" not in url
                    and "q=" not in url
                ):
                    url = search_url
            return ConfirmedAction(
                action="goto",
                risk=step.risk,
                url=url,
                reason=step.reason or step.instruction,
            )
        if step.action == "wait":
            return ConfirmedAction(
                action="wait",
                risk=step.risk,
                wait_ms=1000,
                reason=step.reason or step.instruction,
            )
        if step.action == "extract":
            fields = step.extract_fields or ["url", "title", "text_preview"]
            result_type = intent.result_type if intent else None
            if result_type == "urls":
                fields = ["formatted", "count", "items"]
            elif result_type == "title":
                fields = ["title", "url"]
            elif result_type == "text":
                fields = ["text", "url", "title"]
            return ConfirmedAction(
                action="extract",
                risk=step.risk,
                extract_fields=fields,
                reason=step.reason or step.instruction,
                result_type=result_type,
            )

        # click / type / select は観察 → Lite 確定
        self._emit("observe", "ページを観察します")
        observation = observe_page(page, max_candidates=self.observe_max_candidates)
        self._emit(
            "observe",
            "観察完了",
            candidate_count=observation.candidate_count,
            url=observation.url,
        )

        self._emit("resolve", "要素を確定します", instruction=step.instruction)
        resolved = self.gemini.resolve_element(
            step.instruction,
            observation,
            preferred_action=step.action,
        )
        self._emit("resolve", "要素確定", resolution=resolved.to_dict())

        if resolved.action == "ask_user" or resolved.candidate_id in {"", "none"}:
            return ConfirmedAction(
                action="ask_user",
                risk=resolved.risk,
                reason=resolved.reason or "候補を確定できませんでした",
            )

        selector = _selector_from_resolution(observation, resolved)
        if not selector:
            raise ActionError(
                f"セレクタを作れませんでした: candidate_id={resolved.candidate_id}"
            )

        text = resolved.text if resolved.text is not None else step.text
        if text is None and intent and intent.query_hint and step.action == "type":
            text = intent.query_hint

        press_enter = _should_press_enter(step, intent=intent, text=text)

        return ConfirmedAction(
            action=resolved.action if resolved.action in {"click", "type", "select"} else step.action,
            risk=resolved.risk or step.risk,
            selector=selector,
            text=text,
            reason=resolved.reason or step.reason or step.instruction,
            press_enter=press_enter,
        )


def _should_press_enter(
    step: PlanStep,
    *,
    intent: NormalizedIntent | None,
    text: str | None,
) -> bool:
    if step.action != "type":
        return False
    blob = f"{step.instruction or ''} {step.reason or ''}"
    if "\n" in (text or "") or "\n" in (step.text or ""):
        return True
    if any(k in blob for k in ("Enter", "エンター", "enter", "検索を実行", "送信して検索")):
        return True
    # 検索クエリ入力 → Enter で結果へ（検索ボタン click より安定）
    if intent and intent.query_hint and intent.result_type == "urls":
        t = (text or "").strip()
        if t == intent.query_hint.strip() or "検索" in blob or "入力" in blob:
            return True
    return False


def _selector_from_resolution(
    observation: Observation,
    resolved: ElementResolution,
) -> str | None:
    if resolved.selector_hint:
        return resolved.selector_hint
    for cand in observation.candidates:
        if cand.id == resolved.candidate_id:
            if cand.selector_hints:
                return cand.selector_hints[0]
            break
    return None


def _extract_url_from_text(text: str) -> str | None:
    import re

    m = re.search(r"https?://\S+", text or "")
    return m.group(0).rstrip(")、。,.>") if m else None


def run_instruction_on_browser(
    *,
    browser: Any,
    instruction: str,
    gemini: GeminiClient,
    executor: ActionExecutor,
    observe_max_candidates: int = 40,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    replan_on_failure: bool = True,
    stop_event: threading.Event | None = None,
) -> AgentRunResult:
    """ブラウザ接続済み前提の実行ヘルパー."""
    page = get_active_page(browser)
    loop = AgentLoop(
        gemini=gemini,
        executor=executor,
        max_consecutive_failures=max_consecutive_failures,
        replan_on_failure=replan_on_failure,
        observe_max_candidates=observe_max_candidates,
    )
    if stop_event is not None:
        # 外部 Event と共有
        loop._stop = stop_event  # noqa: SLF001

    def _on_event(event: AgentEvent) -> None:
        print(f"[{event.phase}] {event.message}")

    loop.on_event = _on_event
    return loop.run(page, instruction)
