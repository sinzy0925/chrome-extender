"""Phase 4: Gemini スキーマ・クライアントのテスト."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from browser_assistant.config import Settings
from browser_assistant.gemini_client import GeminiClient, GeminiError
from browser_assistant.schemas import (
    parse_element_resolution,
    parse_step_plan,
    SchemaValidationError,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        gemini_api_key="test-key",
        gemini_model_flash="gemini-flash-test",
        gemini_model_flash_lite="gemini-lite-test",
        cdp_host="127.0.0.1",
        cdp_port=9222,
        user_data_dir=tmp_path / "user-data",
        log_level="INFO",
        app_dir=tmp_path,
        env_path=tmp_path / ".env",
        chrome_path=None,
        keep_browser_open=True,
        observe_max_candidates=40,
        max_steps=30,
        post_action_wait_ms=500,
    )


def test_parse_step_plan_requires_risk() -> None:
    with pytest.raises(SchemaValidationError, match="risk"):
        parse_step_plan(
            {
                "steps": [
                    {
                        "id": "1",
                        "instruction": "開く",
                        "action": "goto",
                        # risk missing
                    }
                ]
            }
        )


def test_parse_element_resolution_requires_risk() -> None:
    with pytest.raises(SchemaValidationError, match="risk"):
        parse_element_resolution(
            {
                "candidate_id": "e1",
                "action": "click",
                "reason": "ボタン",
            }
        )


def test_plan_steps_with_mock_client(tmp_path: Path) -> None:
    payload = {
        "steps": [
            {
                "id": "1",
                "instruction": "example.com を開く",
                "action": "goto",
                "risk": "low",
                "reason": "URLを開く",
                "url": "https://example.com",
            },
            {
                "id": "2",
                "instruction": "見出しを確認",
                "action": "extract",
                "risk": "low",
                "reason": "情報収集",
                "extract_fields": ["title"],
            },
            {
                "id": "3",
                "instruction": "完了",
                "action": "done",
                "risk": "low",
                "reason": "終了",
            },
        ]
    }
    mock_models = MagicMock()
    mock_models.generate_content.return_value = SimpleNamespace(
        text=json.dumps(payload, ensure_ascii=False),
        candidates=[],
    )
    mock_client = MagicMock()
    mock_client.models = mock_models

    client = GeminiClient(_settings(tmp_path), client=mock_client)
    plan = client.plan_steps("https://example.com のタイトルを取って")
    assert len(plan.steps) >= 2
    assert plan.steps[0].action == "goto"
    assert all(s.risk in {"low", "high"} for s in plan.steps)
    mock_models.generate_content.assert_called_once()


def test_plan_steps_rejects_single_coarse_step(tmp_path: Path) -> None:
    payload = {
        "steps": [
            {
                "id": "1",
                "instruction": "全部やる",
                "action": "click",
                "risk": "low",
                "reason": "雑",
            }
        ]
    }
    mock_models = MagicMock()
    mock_models.generate_content.return_value = SimpleNamespace(
        text=json.dumps(payload),
        candidates=[],
    )
    mock_client = MagicMock()
    mock_client.models = mock_models
    client = GeminiClient(_settings(tmp_path), client=mock_client)
    with pytest.raises(GeminiError, match="粗すぎ"):
        client.plan_steps("いい感じに色々して")


def test_resolve_element_with_mock(tmp_path: Path) -> None:
    payload = {
        "candidate_id": "e2",
        "action": "click",
        "risk": "low",
        "reason": "検索ボタン",
        "selector_hint": "text=検索",
        "confidence": 0.9,
    }
    mock_models = MagicMock()
    mock_models.generate_content.return_value = SimpleNamespace(
        text=json.dumps(payload, ensure_ascii=False),
        candidates=[],
    )
    mock_client = MagicMock()
    mock_client.models = mock_models
    client = GeminiClient(_settings(tmp_path), client=mock_client)
    obs = {
        "url": "https://example.com",
        "title": "Example",
        "candidates": [
            {"id": "e1", "tag": "a", "name": "Home", "text": "Home", "visible": True},
            {
                "id": "e2",
                "tag": "button",
                "name": "検索",
                "text": "検索",
                "visible": True,
            },
        ],
    }
    resolved = client.resolve_element("検索ボタンを押す", obs)
    assert resolved.candidate_id == "e2"
    assert resolved.risk == "low"
    assert resolved.action == "click"


def test_api_failure_becomes_gemini_error(tmp_path: Path) -> None:
    mock_models = MagicMock()
    mock_models.generate_content.side_effect = RuntimeError("429 rate limit exceeded")
    mock_client = MagicMock()
    mock_client.models = mock_models
    client = GeminiClient(_settings(tmp_path), client=mock_client)
    with pytest.raises(GeminiError, match="レート制限"):
        client.plan_steps("テスト")


def test_invalid_api_key_message(tmp_path: Path) -> None:
    mock_models = MagicMock()
    mock_models.generate_content.side_effect = RuntimeError("API key not valid")
    mock_client = MagicMock()
    mock_client.models = mock_models
    client = GeminiClient(_settings(tmp_path), client=mock_client)
    with pytest.raises(GeminiError, match="認証"):
        client.plan_steps("テスト")


@pytest.mark.integration
def test_live_flash_plan_and_lite_resolve(tmp_path: Path) -> None:
    """有効な GEMINI_API_KEY があるときのみ実行."""
    from browser_assistant.config import load_settings
    from browser_assistant.paths import get_app_dir

    env_path = get_app_dir() / ".env"
    if not env_path.is_file():
        pytest.skip(".env がありません")
    try:
        settings = load_settings(require_api_key=True)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"設定を読めません: {exc}")
    if not settings.gemini_api_key or settings.gemini_api_key.startswith("your_"):
        pytest.skip("有効な GEMINI_API_KEY がありません")

    client = GeminiClient(settings)
    plan = client.plan_steps(
        "https://example.com を開いて、ページのタイトル情報を収集して"
    )
    assert len(plan.steps) >= 2
    assert all(s.risk in {"low", "high"} for s in plan.steps)

    obs = {
        "url": "https://example.com",
        "title": "Example Domain",
        "candidates": [
            {
                "id": "e1",
                "tag": "a",
                "name": "More information...",
                "text": "More information...",
                "href": "https://www.iana.org/domains/example",
                "visible": True,
                "selector_hints": ["text=More information..."],
            },
            {
                "id": "e2",
                "tag": "h1",
                "name": "Example Domain",
                "text": "Example Domain",
                "visible": True,
                "selector_hints": [],
            },
        ],
    }
    # h1 は観察候補に通常出ないが、マッチング用にリンクを選ばせる
    resolved = client.resolve_element("More information リンクをクリック", obs)
    assert resolved.risk in {"low", "high"}
    assert resolved.candidate_id
    assert resolved.reason
