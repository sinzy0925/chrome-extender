"""Gemini 入出力の固定スキーマ."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ActionType = Literal[
    "click",
    "type",
    "select",
    "goto",
    "extract",
    "wait",
    "done",
    "ask_user",
]
RiskLevel = Literal["low", "high"]

ALLOWED_ACTIONS: frozenset[str] = frozenset(
    {"click", "type", "select", "goto", "extract", "wait", "done", "ask_user"}
)
ALLOWED_RISKS: frozenset[str] = frozenset({"low", "high"})

STEP_PLAN_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "instruction": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": sorted(ALLOWED_ACTIONS),
                    },
                    "risk": {"type": "string", "enum": ["low", "high"]},
                    "reason": {"type": "string"},
                    "url": {"type": "string"},
                    "text": {"type": "string"},
                    "extract_fields": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["id", "instruction", "action", "risk"],
            },
        }
    },
    "required": ["steps"],
}

ELEMENT_RESOLVE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidate_id": {"type": "string"},
        "action": {
            "type": "string",
            "enum": ["click", "type", "select", "extract", "ask_user"],
        },
        "risk": {"type": "string", "enum": ["low", "high"]},
        "reason": {"type": "string"},
        "text": {"type": "string"},
        "selector_hint": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["candidate_id", "action", "risk", "reason"],
}


@dataclass
class PlanStep:
    id: str
    instruction: str
    action: str
    risk: str
    reason: str = ""
    url: str | None = None
    text: str | None = None
    extract_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StepPlan:
    steps: list[PlanStep]

    def to_dict(self) -> dict[str, Any]:
        return {"steps": [s.to_dict() for s in self.steps]}


@dataclass
class ElementResolution:
    candidate_id: str
    action: str
    risk: str
    reason: str
    text: str | None = None
    selector_hint: str | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SchemaValidationError(ValueError):
    """モデル出力がスキーマ要件を満たさない."""


def _require_keys(data: dict[str, Any], keys: list[str], *, ctx: str) -> None:
    missing: list[str] = []
    for k in keys:
        if k not in data:
            missing.append(k)
            continue
        if k == "risk" and data[k] not in ALLOWED_RISKS:
            missing.append(k)
            continue
        if k != "risk" and data[k] in (None, ""):
            missing.append(k)
    if missing:
        raise SchemaValidationError(f"{ctx}: 必須フィールド不足または不正: {missing}")


def parse_step_plan(data: dict[str, Any]) -> StepPlan:
    if not isinstance(data, dict) or "steps" not in data:
        raise SchemaValidationError("steps 配列がありません")
    steps_raw = data["steps"]
    if not isinstance(steps_raw, list) or not steps_raw:
        raise SchemaValidationError("steps は1件以上必要です")

    steps: list[PlanStep] = []
    for i, raw in enumerate(steps_raw):
        if not isinstance(raw, dict):
            raise SchemaValidationError(f"steps[{i}] がオブジェクトではありません")
        _require_keys(raw, ["id", "instruction", "action", "risk"], ctx=f"steps[{i}]")
        action = str(raw["action"]).strip()
        risk = str(raw["risk"]).strip().lower()
        if action not in ALLOWED_ACTIONS:
            raise SchemaValidationError(f"steps[{i}]: 未知の action: {action}")
        if risk not in ALLOWED_RISKS:
            raise SchemaValidationError(f"steps[{i}]: risk は low|high 必須（got={risk!r}）")
        fields = raw.get("extract_fields") or []
        if not isinstance(fields, list):
            fields = []
        steps.append(
            PlanStep(
                id=str(raw["id"]),
                instruction=str(raw["instruction"]),
                action=action,
                risk=risk,
                reason=str(raw.get("reason") or ""),
                url=(str(raw["url"]) if raw.get("url") else None),
                text=(str(raw["text"]) if raw.get("text") is not None else None),
                extract_fields=[str(x) for x in fields],
            )
        )
    return StepPlan(steps=steps)


def parse_element_resolution(data: dict[str, Any]) -> ElementResolution:
    if not isinstance(data, dict):
        raise SchemaValidationError("要素確定結果がオブジェクトではありません")
    _require_keys(
        data,
        ["candidate_id", "action", "risk", "reason"],
        ctx="element_resolution",
    )
    action = str(data["action"]).strip()
    risk = str(data["risk"]).strip().lower()
    if action not in {"click", "type", "select", "extract", "ask_user"}:
        raise SchemaValidationError(f"未知の action: {action}")
    if risk not in ALLOWED_RISKS:
        raise SchemaValidationError(f"risk は low|high 必須（got={risk!r}）")
    conf = data.get("confidence")
    confidence = float(conf) if conf is not None else None
    return ElementResolution(
        candidate_id=str(data["candidate_id"]),
        action=action,
        risk=risk,
        reason=str(data["reason"]),
        text=(str(data["text"]) if data.get("text") is not None else None),
        selector_hint=(
            str(data["selector_hint"]) if data.get("selector_hint") else None
        ),
        confidence=confidence,
    )
