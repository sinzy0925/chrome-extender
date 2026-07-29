"""Phase 9: デスクトップUI関連のテスト."""

from __future__ import annotations

from pathlib import Path

import pytest

from browser_assistant.config import Settings
from browser_assistant.executor import ConfirmedAction
from browser_assistant.export import CollectionStore
from browser_assistant.safety import ConfirmDecision, SafetyVerdict
from browser_assistant.ui_app import LOGIN_BANNER, validate_instruction

tk = pytest.importorskip("tkinter")


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        gemini_api_key="test",
        gemini_model_flash="flash",
        gemini_model_flash_lite="lite",
        cdp_host="127.0.0.1",
        cdp_port=9222,
        user_data_dir=tmp_path / "user-data",
        log_level="INFO",
        app_dir=tmp_path,
        env_path=tmp_path / ".env",
    )


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"tkinter が利用できません: {exc}")
    root.withdraw()
    try:
        yield root
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_empty_instruction_is_rejected() -> None:
    assert validate_instruction("") is not None
    assert validate_instruction("   ") is not None
    assert validate_instruction("開いて収集して") is None


def test_ui_shows_login_banner_and_controls(tk_root, tmp_path: Path) -> None:
    from browser_assistant.ui_app import BrowserAssistantApp

    app = BrowserAssistantApp(tk_root, _settings(tmp_path))
    assert LOGIN_BANNER in app.banner.cget("text")
    assert "事前ログイン" in app.banner.cget("text")
    assert app.btn_start is not None
    assert str(app.btn_stop["state"]) == "disabled"
    assert app.format_var.get() in {"json", "csv"}


def test_start_with_empty_instruction_warns(
    tk_root, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from browser_assistant.ui_app import BrowserAssistantApp

    warned: list[str] = []

    def fake_warn(title, message, **kwargs):  # noqa: ANN001
        warned.append(message)
        return "ok"

    monkeypatch.setattr("browser_assistant.ui_app.messagebox.showwarning", fake_warn)
    app = BrowserAssistantApp(tk_root, _settings(tmp_path))
    app.instruction.delete("1.0", tk.END)
    app.on_start()
    assert warned and "空" in warned[0]
    assert not app._running


def test_stop_enabled_only_while_running(tk_root, tmp_path: Path) -> None:
    from browser_assistant.ui_app import BrowserAssistantApp

    app = BrowserAssistantApp(tk_root, _settings(tmp_path))
    assert str(app.btn_stop["state"]) == "disabled"
    app._set_running(True)
    assert str(app.btn_stop["state"]) == "normal"
    app._set_running(False)
    assert str(app.btn_stop["state"]) == "disabled"

    # ログ / ステップ表示の更新経路
    app._queue.put(("log", "[step] demo"))
    app._queue.put(("step", "デモ手順"))
    app._poll_queue()
    assert "demo" in app.log.get("1.0", tk.END)
    assert "デモ手順" in app.step_var.get()


def test_confirm_dialog_sets_decision(
    tk_root, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from browser_assistant.ui_app import BrowserAssistantApp

    app = BrowserAssistantApp(tk_root, _settings(tmp_path))
    real_toplevel = tk.Toplevel

    def fake_toplevel(*args, **kwargs):  # noqa: ANN003
        dlg = real_toplevel(*args, **kwargs)

        def _auto():
            app._confirm_decision = ConfirmDecision.EXECUTE
            app._confirm_event.set()
            dlg.destroy()

        dlg.after(10, _auto)
        return dlg

    monkeypatch.setattr("browser_assistant.ui_app.tk.Toplevel", fake_toplevel)
    action = ConfirmedAction(action="click", risk="high", reason="削除", selector="#x")
    verdict = SafetyVerdict(needs_confirmation=True, reasons=("risk=high",))
    app._show_confirm_dialog(action, verdict)
    assert app._confirm_decision == ConfirmDecision.EXECUTE


def test_export_json_csv_from_ui_store(
    tk_root, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from browser_assistant.ui_app import BrowserAssistantApp

    infos: list[str] = []
    monkeypatch.setattr(
        "browser_assistant.ui_app.messagebox.showinfo",
        lambda *a, **k: infos.append(a[1] if len(a) > 1 else str(a)),
    )
    monkeypatch.setattr(
        "browser_assistant.ui_app.messagebox.showwarning",
        lambda *a, **k: infos.append("warn"),
    )

    app = BrowserAssistantApp(tk_root, _settings(tmp_path))
    app.on_export()
    assert "warn" in infos

    app._store = CollectionStore()
    app._store.add({"title": "T", "url": "https://example.com"})
    app._set_preview(app._store.preview())
    assert "T" in app.preview.get("1.0", tk.END)

    out_json = tmp_path / "ui.json"
    monkeypatch.setattr(
        "browser_assistant.ui_app.filedialog.asksaveasfilename",
        lambda **k: str(out_json),
    )
    app.format_var.set("json")
    app.on_export()
    assert out_json.is_file()
    assert "T" in out_json.read_text(encoding="utf-8")

    out_csv = tmp_path / "ui.csv"
    monkeypatch.setattr(
        "browser_assistant.ui_app.filedialog.asksaveasfilename",
        lambda **k: str(out_csv),
    )
    app.format_var.set("csv")
    app.on_export()
    assert out_csv.read_bytes().startswith(b"\xef\xbb\xbf")
