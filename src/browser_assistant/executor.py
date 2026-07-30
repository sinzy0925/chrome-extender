"""確定済みアクションを1手だけ実行するエンジン."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from browser_assistant.observe import Observation, observe_page, take_snapshot
from browser_assistant.schemas import ALLOWED_ACTIONS

logger = logging.getLogger("browser_assistant.executor")

DEFAULT_MAX_STEPS = 30
DEFAULT_POST_WAIT_MS = 500
DEFAULT_ELEMENT_TIMEOUT_MS = 5000


class ActionError(Exception):
    """1手の実行失敗（リトライしない。理由を持つ）."""

    def __init__(self, message: str, *, action: str | None = None) -> None:
        super().__init__(message)
        self.action = action
        self.message = message


@dataclass
class ConfirmedAction:
    """AI確定後（またはテスト用）の1手."""

    action: str
    risk: str = "low"
    selector: str | None = None
    url: str | None = None
    text: str | None = None
    wait_ms: int | None = None
    extract_fields: list[str] = field(default_factory=list)
    reason: str = ""
    result_type: str | None = None
    press_enter: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConfirmedAction:
        if not isinstance(data, dict):
            raise ActionError("アクションはオブジェクトである必要があります")
        action = str(data.get("action") or "").strip()
        if action not in ALLOWED_ACTIONS:
            raise ActionError(f"未知または未対応の action: {action!r}", action=action or None)
        fields = data.get("extract_fields") or []
        if not isinstance(fields, list):
            fields = []
        wait_ms = data.get("wait_ms")
        rt = data.get("result_type")
        return cls(
            action=action,
            risk=str(data.get("risk") or "low"),
            selector=(str(data["selector"]) if data.get("selector") else None),
            url=(str(data["url"]) if data.get("url") else None),
            text=(None if data.get("text") is None else str(data.get("text"))),
            wait_ms=int(wait_ms) if wait_ms is not None else None,
            extract_fields=[str(x) for x in fields],
            reason=str(data.get("reason") or ""),
            result_type=(str(rt).strip() if rt else None),
            press_enter=bool(data.get("press_enter")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ActionResult:
    ok: bool
    action: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    observation: Observation | None = None
    snapshot: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "ok": self.ok,
            "action": self.action,
            "message": self.message,
            "data": self.data,
            "snapshot": self.snapshot,
        }
        if self.observation is not None:
            out["observation"] = self.observation.to_dict()
        return out


class ActionExecutor:
    """最大ステップ数を管理しつつ、1回の呼び出しで1手だけ実行する."""

    def __init__(
        self,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        post_wait_ms: int = DEFAULT_POST_WAIT_MS,
        element_timeout_ms: int = DEFAULT_ELEMENT_TIMEOUT_MS,
        reobserve: bool = True,
        max_candidates: int = 40,
        extract_url_limit: int = 50,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps は 1 以上である必要があります")
        self.max_steps = max_steps
        self.post_wait_ms = max(0, post_wait_ms)
        self.element_timeout_ms = max(0, element_timeout_ms)
        self.reobserve = reobserve
        self.max_candidates = max_candidates
        self.extract_url_limit = max(1, extract_url_limit)
        self.steps_executed = 0

    def remaining_steps(self) -> int:
        return max(0, self.max_steps - self.steps_executed)

    def run_one(self, page: Any, action: ConfirmedAction | dict[str, Any]) -> ActionResult:
        """確定アクションをちょうど1手実行する（複数手は実行しない）."""
        if self.steps_executed >= self.max_steps:
            raise ActionError(
                f"最大ステップ数（{self.max_steps}）を超えました。実行を打ち切ります。"
            )

        confirmed = (
            action if isinstance(action, ConfirmedAction) else ConfirmedAction.from_dict(action)
        )
        logger.info(
            "実行(1手): action=%s risk=%s selector=%s url=%s reason=%s",
            confirmed.action,
            confirmed.risk,
            confirmed.selector,
            confirmed.url,
            confirmed.reason,
        )

        try:
            result = dispatch_action(
                page,
                confirmed,
                element_timeout_ms=self.element_timeout_ms,
                extract_url_limit=self.extract_url_limit,
            )
        except ActionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ActionError(f"実行中にエラー: {exc}", action=confirmed.action) from exc

        self.steps_executed += 1

        if self.post_wait_ms:
            time.sleep(self.post_wait_ms / 1000.0)

        # 実行後フック: スナップショット + 任意で再観察
        try:
            snap = take_snapshot(page)
            result.snapshot = asdict(snap)
            if self.reobserve and confirmed.action not in {"done", "ask_user"}:
                result.observation = observe_page(page, max_candidates=self.max_candidates)
        except Exception as exc:  # noqa: BLE001
            logger.warning("実行後の再観察に失敗しました: %s", exc)

        logger.info(
            "実行完了: action=%s steps=%s/%s msg=%s",
            confirmed.action,
            self.steps_executed,
            self.max_steps,
            result.message,
        )
        return result


def dispatch_action(
    page: Any,
    action: ConfirmedAction,
    *,
    element_timeout_ms: int = DEFAULT_ELEMENT_TIMEOUT_MS,
    extract_url_limit: int = 50,
) -> ActionResult:
    """ページに対して1アクションだけ適用する."""
    name = action.action

    if name == "goto":
        if not action.url:
            raise ActionError("goto には url が必要です", action=name)
        page.goto(action.url, wait_until="domcontentloaded")
        return ActionResult(ok=True, action=name, message=f"opened {action.url}", data={"url": page.url})

    if name == "wait":
        ms = action.wait_ms if action.wait_ms is not None else 1000
        time.sleep(max(0, ms) / 1000.0)
        return ActionResult(ok=True, action=name, message=f"waited {ms}ms", data={"wait_ms": ms})

    if name == "done":
        return ActionResult(ok=True, action=name, message="done", data={})

    if name == "ask_user":
        return ActionResult(
            ok=True,
            action=name,
            message="ユーザー確認が必要です（自動では進めません）",
            data={"reason": action.reason},
        )

    if name == "extract":
        from browser_assistant.extract_data import extract_by_result_type

        data = extract_by_result_type(
            page,
            action.result_type,
            extract_fields=action.extract_fields,
            url_limit=extract_url_limit,
        )
        return ActionResult(ok=True, action=name, message="extracted", data=data)

    if name in {"click", "type", "select"}:
        if not action.selector:
            raise ActionError(f"{name} には selector が必要です", action=name)
        locator = page.locator(action.selector).first
        try:
            count = locator.count()
        except Exception as exc:  # noqa: BLE001
            raise ActionError(
                f"セレクタを解決できません: {action.selector} ({exc})",
                action=name,
            ) from exc
        if count == 0:
            raise ActionError(
                f"要素が見つかりません: selector={action.selector!r}",
                action=name,
            )

        try:
            if name == "click":
                try:
                    locator.click(timeout=element_timeout_ms)
                except Exception as click_exc:  # noqa: BLE001
                    # 直前の Enter 送信などで既に検索結果へ遷移済みなら成功扱い
                    current = str(getattr(page, "url", "") or "")
                    if _looks_like_serp(current):
                        logger.info(
                            "click 失敗だが検索結果ページのため続行: url=%s err=%s",
                            current,
                            click_exc,
                        )
                        return ActionResult(
                            ok=True,
                            action=name,
                            message="click skipped; already on search results",
                            data={"selector": action.selector, "page_url": current},
                        )
                    raise
                return ActionResult(
                    ok=True,
                    action=name,
                    message=f"clicked {action.selector}",
                    data={"selector": action.selector},
                )
            if name == "type":
                if action.text is None:
                    raise ActionError("type には text が必要です", action=name)
                raw_text = action.text
                submit = bool(action.press_enter)
                if "\n" in raw_text or "\r" in raw_text:
                    submit = True
                text = raw_text.replace("\r", "").replace("\n", "")
                locator.fill(text, timeout=element_timeout_ms)
                if submit:
                    locator.press("Enter", timeout=element_timeout_ms)
                    # 遷移待ち（短い）
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=element_timeout_ms)
                    except Exception:  # noqa: BLE001
                        pass
                return ActionResult(
                    ok=True,
                    action=name,
                    message=(
                        f"typed into {action.selector}"
                        + (" + Enter" if submit else "")
                    ),
                    data={
                        "selector": action.selector,
                        "text": text,
                        "press_enter": submit,
                    },
                )
            # select
            if action.text is None:
                raise ActionError("select には text（option値/ラベル）が必要です", action=name)
            locator.select_option(action.text, timeout=element_timeout_ms)
            return ActionResult(
                ok=True,
                action=name,
                message=f"selected {action.text} on {action.selector}",
                data={"selector": action.selector, "text": action.text},
            )
        except ActionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ActionError(
                f"{name} に失敗: selector={action.selector!r} ({exc})",
                action=name,
            ) from exc

    raise ActionError(f"未実装の action: {name}", action=name)


def _looks_like_serp(url: str) -> bool:
    u = (url or "").lower()
    if "/search" in u:
        return True
    if any(h in u for h in ("google.", "bing.", "yahoo.")) and ("q=" in u or "p=" in u):
        return True
    return False
