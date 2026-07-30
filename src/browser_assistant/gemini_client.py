"""Gemini API クライアント（最新公式 SDK: google-genai）."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from browser_assistant.config import Settings
from browser_assistant.observe import Observation
from browser_assistant.schemas import (
    ELEMENT_RESOLVE_RESPONSE_SCHEMA,
    STEP_PLAN_RESPONSE_SCHEMA,
    ElementResolution,
    SchemaValidationError,
    StepPlan,
    parse_element_resolution,
    parse_step_plan,
)

logger = logging.getLogger("browser_assistant.gemini")

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.I)


class GeminiError(Exception):
    """API 呼び出し・応答処理のユーザー向けエラー."""


def _extract_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise GeminiError("モデル応答が空です")
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise GeminiError(f"JSON を解析できません: {text[:200]}") from None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise GeminiError(f"JSON を解析できません: {exc}") from exc
    if not isinstance(data, dict):
        raise GeminiError("JSON オブジェクト以外が返されました")
    return data


class GeminiClient:
    """Flash=手順分解 / Flash-Lite=要素確定."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self.settings = settings
        if client is not None:
            self._client = client
        else:
            try:
                from google import genai
            except ImportError as exc:
                raise GeminiError(
                    "google-genai がインストールされていません。"
                    " 最新の公式 SDK を `pip install -U google-genai` で入れてください。"
                ) from exc
            if not settings.gemini_api_key:
                raise GeminiError(
                    "GEMINI_API_KEY が未設定です。.env にユーザー自身のキーを記入してください。"
                )
            self._client = genai.Client(api_key=settings.gemini_api_key)

    def _generate_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        response_schema: dict[str, Any],
    ) -> dict[str, Any]:
        from google.genai import types

        try:
            response = self._client.models.generate_content(
                model=model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    temperature=0.2,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            lower = msg.lower()
            if "api key" in lower or "permission" in lower or "unauthenticated" in lower:
                raise GeminiError(
                    "Gemini API 認証に失敗しました。APIキーが正しいか確認してください。"
                ) from exc
            if "429" in msg or "rate" in lower or "quota" in lower:
                raise GeminiError(
                    "Gemini API のレート制限またはクォータに達しました。しばらく待って再試行してください。"
                ) from exc
            raise GeminiError(f"Gemini API 呼び出しに失敗しました: {exc}") from exc

        text = getattr(response, "text", None) or ""
        if not text and getattr(response, "candidates", None):
            # 念のため candidates から拾う
            try:
                parts = response.candidates[0].content.parts
                text = "".join(getattr(p, "text", "") or "" for p in parts)
            except Exception:  # noqa: BLE001
                text = ""
        return _extract_json_object(text)

    def plan_steps(
        self,
        instruction: str,
        *,
        current_url: str | None = None,
        intent: dict[str, Any] | None = None,
    ) -> StepPlan:
        """Flash: 日本語指示を細かいステップ配列に分解する."""
        instruction = (instruction or "").strip()
        if not instruction:
            raise GeminiError("指示文が空です")

        from browser_assistant.intent.normalize import PLANNER_RULES

        system = (
            "あなたはブラウザ自動操作のプランナーです。"
            "ユーザーの日本語指示を、1手ずつ実行できる細かいステップに分解してください。"
            "intent（正規化済み意図）が付いている場合は、それを最優先の前提にしてください。"
            "あいまいな指示でも、可能な範囲で具体的な複数ステップに分けてください。"
            "1ステップに複数操作を詰め込まないでください。"
            "各ステップに action と risk(low|high) を必ず付けてください。"
            "削除・購入・送信・退会・決済・振込などは risk=high にしてください。"
            "intent.target_url または指示に URL がある場合、早い段階で goto を入れてください。"
            "current_url が似ていても、検索・再取得が指示にあれば省略しないでください。"
            "完了時は最後に action=done のステップを入れてください。"
            "不明点がある場合は ask_user ステップを入れてください。"
            "出力は指定JSONスキーマのみ。"
        )
        normalized = None
        if isinstance(intent, dict):
            normalized = intent.get("normalized_instruction") or instruction
        user = {
            "user_instruction": instruction,
            "normalized_instruction": normalized or instruction,
            "current_url": current_url,
            "intent": intent,
            "planner_rules": PLANNER_RULES,
            "allowed_actions": sorted(
                [
                    "click",
                    "type",
                    "select",
                    "goto",
                    "extract",
                    "wait",
                    "done",
                    "ask_user",
                ]
            ),
        }
        data = self._generate_json(
            model=self.settings.gemini_model_flash,
            system=system,
            user=json.dumps(user, ensure_ascii=False),
            response_schema=STEP_PLAN_RESPONSE_SCHEMA,
        )
        try:
            plan = parse_step_plan(data)
        except SchemaValidationError as exc:
            raise GeminiError(f"手順分解の結果が不正です: {exc}") from exc

        # 粗い1手だけの分解は拒否（done/ask_user 単独は許容）
        actionable = [s for s in plan.steps if s.action not in {"done"}]
        if len(actionable) < 1:
            raise GeminiError("実行可能なステップがありません")
        if len(plan.steps) == 1 and plan.steps[0].action not in {"done", "ask_user"}:
            raise GeminiError(
                "手順が粗すぎます（1ステップのみ）。指示をより細かく分解できませんでした。"
            )
        logger.info("手順分解完了: steps=%s", len(plan.steps))
        return plan

    def resolve_element(
        self,
        step_instruction: str,
        observation: Observation | dict[str, Any],
        *,
        preferred_action: str | None = None,
    ) -> ElementResolution:
        """Flash-Lite: 1ステップ + 候補一覧から要素を確定する."""
        step_instruction = (step_instruction or "").strip()
        if not step_instruction:
            raise GeminiError("ステップ指示が空です")

        if isinstance(observation, Observation):
            obs = observation.to_dict()
        else:
            obs = observation

        # Gemini に渡す候補は要約のみ
        slim_candidates = []
        for c in obs.get("candidates") or []:
            slim_candidates.append(
                {
                    "id": c.get("id"),
                    "tag": c.get("tag"),
                    "role": c.get("role"),
                    "name": c.get("name"),
                    "text": c.get("text"),
                    "input_type": c.get("input_type"),
                    "placeholder": c.get("placeholder"),
                    "href": c.get("href"),
                    "selector_hints": c.get("selector_hints"),
                    "visible": c.get("visible"),
                }
            )

        system = (
            "あなたはブラウザ要素のマッチャーです。"
            "与えられた1ステップ指示と候補一覧だけを見て、操作対象の candidate_id を1つ選んでください。"
            "候補に無い要素を捏造しないでください。"
            "確信が持てない場合は action=ask_user、candidate_id は最も近い候補か 'none' にしてください。"
            "risk(low|high) と reason は必須です。"
            "削除・購入・送信などは risk=high です。"
            "出力は指定JSONスキーマのみ。"
        )
        user = {
            "step_instruction": step_instruction,
            "preferred_action": preferred_action,
            "page": {
                "url": obs.get("url"),
                "title": obs.get("title"),
            },
            "candidates": slim_candidates,
        }
        data = self._generate_json(
            model=self.settings.gemini_model_flash_lite,
            system=system,
            user=json.dumps(user, ensure_ascii=False),
            response_schema=ELEMENT_RESOLVE_RESPONSE_SCHEMA,
        )
        try:
            resolved = parse_element_resolution(data)
        except SchemaValidationError as exc:
            raise GeminiError(f"要素確定の結果が不正です: {exc}") from exc
        logger.info(
            "要素確定: id=%s action=%s risk=%s",
            resolved.candidate_id,
            resolved.action,
            resolved.risk,
        )
        return resolved
