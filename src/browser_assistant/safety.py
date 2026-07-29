"""危険操作ガード — 確認待ち / 収集モード制限."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from browser_assistant.executor import ConfirmedAction

# 危険っぽい文言（日本語・英語の代表例）。過剰停止は許容（安全側）。
DANGEROUS_KEYWORDS: tuple[str, ...] = (
    "削除",
    "消去",
    "破棄",
    "退会",
    "解約",
    "購入",
    "決済",
    "支払",
    "注文確定",
    "送信",
    "振込",
    "送金",
    "公開",
    "上書き",
    "unsubscribe",
    "delete",
    "remove account",
    "purchase",
    "checkout",
    "pay now",
    "confirm payment",
    "submit",
    "transfer",
)

WRITE_ACTIONS = frozenset({"click", "type", "select"})
READ_ONLY_ACTIONS = frozenset({"goto", "wait", "extract", "done", "ask_user"})


class ConfirmDecision(str, Enum):
    EXECUTE = "execute"
    SKIP = "skip"
    ABORT = "abort"


@dataclass(frozen=True)
class SafetyVerdict:
    needs_confirmation: bool
    reasons: tuple[str, ...]
    blocked_by_collect_mode: bool = False

    @property
    def summary(self) -> str:
        if self.blocked_by_collect_mode:
            return "収集モードのため書き込み系操作はブロックされました"
        if not self.reasons:
            return ""
        return " / ".join(self.reasons)


ConfirmCallback = Callable[[ConfirmedAction, SafetyVerdict], ConfirmDecision]


def _haystack(action: ConfirmedAction) -> str:
    parts = [
        action.action,
        action.risk,
        action.reason or "",
        action.selector or "",
        action.text or "",
        action.url or "",
    ]
    return " ".join(parts).lower()


def match_dangerous_keywords(text: str) -> list[str]:
    found: list[str] = []
    lower = text.lower()
    for kw in DANGEROUS_KEYWORDS:
        if kw.lower() in lower:
            found.append(kw)
    return found


def assess_action(
    action: ConfirmedAction,
    *,
    collect_mode: bool = False,
) -> SafetyVerdict:
    """危険判定。collect_mode 時は書き込み系をブロック対象にする."""
    reasons: list[str] = []

    if collect_mode and action.action in WRITE_ACTIONS:
        return SafetyVerdict(
            needs_confirmation=False,
            reasons=("collect_mode_blocks_write",),
            blocked_by_collect_mode=True,
        )

    if (action.risk or "").lower() == "high":
        reasons.append("risk=high")

    hits = match_dangerous_keywords(_haystack(action))
    if hits:
        reasons.append("dangerous_keywords=" + ",".join(hits))

    # submit 系セレクタも警戒
    sel = (action.selector or "").lower()
    if action.action == "click" and re.search(r"type\s*=\s*['\"]?submit", sel):
        reasons.append("submit_button")

    return SafetyVerdict(
        needs_confirmation=bool(reasons),
        reasons=tuple(reasons),
        blocked_by_collect_mode=False,
    )


def console_confirm(action: ConfirmedAction, verdict: SafetyVerdict) -> ConfirmDecision:
    """CLI 用の確認UI（実行する / スキップ / 中止）."""
    print("\n=== 危険操作の確認 ===")
    print(f"理由: {verdict.summary}")
    print(f"action={action.action} risk={action.risk}")
    if action.selector:
        print(f"selector={action.selector}")
    if action.url:
        print(f"url={action.url}")
    if action.text is not None:
        print(f"text={action.text}")
    if action.reason:
        print(f"detail={action.reason}")
    print("選択肢: [e] 実行する / [s] スキップ / [a] 中止")
    while True:
        raw = input("> ").strip().lower()
        if raw in {"e", "execute", "y", "yes"}:
            return ConfirmDecision.EXECUTE
        if raw in {"s", "skip"}:
            return ConfirmDecision.SKIP
        if raw in {"a", "abort", "n", "no", "q"}:
            return ConfirmDecision.ABORT
        print("e / s / a のいずれかを入力してください")
