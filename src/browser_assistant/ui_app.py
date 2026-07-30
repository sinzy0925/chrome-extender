"""Phase 9: デスクトップUI（tkinter・最低限）."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

from browser_assistant.agent import AgentLoop
from browser_assistant.browser import LOGIN_GUIDE, BrowserError, build_session
from browser_assistant.config import Settings
from browser_assistant.executor import ActionExecutor, ConfirmedAction
from browser_assistant.export import CollectionStore, ExportError
from browser_assistant.gemini_client import GeminiClient, GeminiError
from browser_assistant.observe import get_active_page
from browser_assistant.safety import ConfirmDecision, SafetyVerdict

logger = logging.getLogger("browser_assistant.ui")

LOGIN_BANNER = (
    "【重要】起動した Chrome に、必要なサイトへ先にログインしてください。"
    "事前ログインがないと、ログイン必須ページは操作・収集できません。"
)


def validate_instruction(text: str) -> str | None:
    """空指示ならエラーメッセージ、OKなら None."""
    if not (text or "").strip():
        return "指示が空です。日本語の指示を入力してから開始してください。"
    return None


class BrowserAssistantApp:
    """指示入力・開始/Stop・ログ・確認・エクスポートの最低限UI."""

    def __init__(self, root: tk.Tk, settings: Settings) -> None:
        self.root = root
        self.settings = settings
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._loop: AgentLoop | None = None
        self._running = False
        self._confirm_event = threading.Event()
        self._confirm_decision = ConfirmDecision.ABORT
        self._store = CollectionStore()

        self.root.title("日本語指示ブラウザ助手")
        self.root.geometry("820x640")
        self._build()
        self.root.after(100, self._poll_queue)

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}

        self.banner = tk.Label(
            self.root,
            text=LOGIN_BANNER,
            justify=tk.LEFT,
            wraplength=780,
            fg="#7a1f1f",
            bg="#ffe8e8",
            anchor="w",
        )
        self.banner.pack(fill=tk.X, **pad)

        frm_in = tk.LabelFrame(self.root, text="指示")
        frm_in.pack(fill=tk.X, **pad)
        self.instruction = tk.Text(frm_in, height=4, wrap=tk.WORD)
        self.instruction.pack(fill=tk.X, padx=8, pady=6)

        opts = tk.Frame(frm_in)
        opts.pack(fill=tk.X, padx=8, pady=(0, 6))
        self.collect_mode_var = tk.BooleanVar(value=self.settings.collect_mode)
        tk.Checkbutton(
            opts,
            text="収集モード（書き込みブロック）",
            variable=self.collect_mode_var,
        ).pack(side=tk.LEFT)

        btns = tk.Frame(self.root)
        btns.pack(fill=tk.X, **pad)
        self.btn_start = tk.Button(btns, text="開始", command=self.on_start)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_stop = tk.Button(btns, text="Stop", command=self.on_stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT)

        self.step_var = tk.StringVar(value="現在ステップ: (待機中)")
        tk.Label(self.root, textvariable=self.step_var).pack(anchor="w", padx=10)

        frm_log = tk.LabelFrame(self.root, text="ログ")
        frm_log.pack(fill=tk.BOTH, expand=True, **pad)
        self.log = tk.Text(frm_log, height=12, wrap=tk.WORD, state=tk.DISABLED)
        self.log.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        frm_prev = tk.LabelFrame(self.root, text="収集プレビュー")
        frm_prev.pack(fill=tk.BOTH, expand=False, **pad)
        self.preview = tk.Text(frm_prev, height=6, wrap=tk.WORD, state=tk.DISABLED)
        self.preview.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        export = tk.Frame(self.root)
        export.pack(fill=tk.X, **pad)
        tk.Label(export, text="形式:").pack(side=tk.LEFT)
        self.format_var = tk.StringVar(value="json")
        tk.Radiobutton(export, text="JSON", variable=self.format_var, value="json").pack(
            side=tk.LEFT, padx=4
        )
        tk.Radiobutton(export, text="CSV", variable=self.format_var, value="csv").pack(
            side=tk.LEFT, padx=4
        )
        self.btn_export = tk.Button(export, text="ダウンロード", command=self.on_export)
        self.btn_export.pack(side=tk.LEFT, padx=8)

    def _append_log(self, line: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, line + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _set_preview(self, text: str) -> None:
        self.preview.configure(state=tk.NORMAL)
        self.preview.delete("1.0", tk.END)
        self.preview.insert(tk.END, text)
        self.preview.configure(state=tk.DISABLED)

    def _set_running(self, running: bool) -> None:
        self._running = running
        self.btn_start.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.btn_stop.configure(state=tk.NORMAL if running else tk.DISABLED)

    def on_start(self) -> None:
        err = validate_instruction(self.instruction.get("1.0", tk.END))
        if err:
            messagebox.showwarning("入力エラー", err, parent=self.root)
            return
        if self._running:
            return
        self._store.clear()
        self._set_preview("(実行中…)")
        self._set_running(True)
        self.step_var.set("現在ステップ: 開始処理中…")
        instruction = self.instruction.get("1.0", tk.END).strip()
        collect_mode = bool(self.collect_mode_var.get())
        self._worker = threading.Thread(
            target=self._run_worker,
            args=(instruction, collect_mode),
            daemon=True,
        )
        self._worker.start()

    def on_stop(self) -> None:
        if self._loop is not None:
            self._loop.request_stop()
            self._append_log("[ui] Stop を要求しました")

    def on_export(self) -> None:
        if self._store.is_empty:
            messagebox.showwarning(
                "ダウンロード不可",
                "収集結果が 0 件のためダウンロードできません。",
                parent=self.root,
            )
            return
        fmt = self.format_var.get()
        if fmt == "json":
            path = filedialog.asksaveasfilename(
                parent=self.root,
                defaultextension=".json",
                filetypes=[("JSON", "*.json")],
            )
            if not path:
                return
            try:
                saved = self._store.save_json(Path(path))
                messagebox.showinfo("保存完了", f"JSON を保存しました:\n{saved}", parent=self.root)
            except ExportError as exc:
                messagebox.showwarning("ダウンロード不可", str(exc), parent=self.root)
        else:
            path = filedialog.asksaveasfilename(
                parent=self.root,
                defaultextension=".csv",
                filetypes=[("CSV", "*.csv")],
            )
            if not path:
                return
            try:
                saved = self._store.save_csv(Path(path), with_bom=True)
                messagebox.showinfo("保存完了", f"CSV を保存しました:\n{saved}", parent=self.root)
            except ExportError as exc:
                messagebox.showwarning("ダウンロード不可", str(exc), parent=self.root)

    def _confirm_callback(self, action: ConfirmedAction, verdict: SafetyVerdict) -> ConfirmDecision:
        self._confirm_event.clear()
        self._confirm_decision = ConfirmDecision.ABORT
        self._queue.put(("confirm", (action, verdict)))
        while not self._confirm_event.wait(timeout=0.2):
            if not self._running and self._loop is None:
                break
        return self._confirm_decision

    def _show_confirm_dialog(self, action: ConfirmedAction, verdict: SafetyVerdict) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("危険操作の確認")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.geometry("480x260")

        msg = (
            f"理由: {verdict.summary}\n\n"
            f"action={action.action} risk={action.risk}\n"
            f"selector={action.selector or '-'}\n"
            f"detail={action.reason or '-'}"
        )
        tk.Label(dlg, text=msg, justify=tk.LEFT, wraplength=440).pack(
            padx=12, pady=12, anchor="w"
        )

        def _choose(decision: ConfirmDecision) -> None:
            self._confirm_decision = decision
            self._confirm_event.set()
            dlg.destroy()

        bar = tk.Frame(dlg)
        bar.pack(pady=10)
        tk.Button(bar, text="実行する", command=lambda: _choose(ConfirmDecision.EXECUTE)).pack(
            side=tk.LEFT, padx=6
        )
        tk.Button(bar, text="スキップ", command=lambda: _choose(ConfirmDecision.SKIP)).pack(
            side=tk.LEFT, padx=6
        )
        tk.Button(bar, text="中止", command=lambda: _choose(ConfirmDecision.ABORT)).pack(
            side=tk.LEFT, padx=6
        )

        dlg.protocol("WM_DELETE_WINDOW", lambda: _choose(ConfirmDecision.ABORT))
        dlg.wait_window()

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "step":
                    self.step_var.set(f"現在ステップ: {payload}")
                elif kind == "confirm":
                    action, verdict = payload
                    self._show_confirm_dialog(action, verdict)
                elif kind == "done":
                    result = payload
                    self._store.clear()
                    self._store.extend(result.get("extracts") or [])
                    self._set_preview(self._store.preview())
                    self._set_running(False)
                    self.step_var.set(f"現在ステップ: 終了 ({result.get('status')})")
                    self._append_log(
                        f"[done] status={result.get('status')} {result.get('message')}"
                    )
                elif kind == "error":
                    self._set_running(False)
                    self.step_var.set("現在ステップ: エラー")
                    self._append_log(f"[error] {payload}")
                    messagebox.showerror("実行エラー", str(payload), parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _run_worker(self, instruction: str, collect_mode: bool) -> None:
        try:
            gemini = GeminiClient(self.settings)
            executor = ActionExecutor(
                max_steps=self.settings.max_steps,
                post_wait_ms=self.settings.post_action_wait_ms,
                reobserve=True,
                max_candidates=self.settings.observe_max_candidates,
                extract_url_limit=self.settings.intent_extract_url_limit,
            )

            def on_event(event) -> None:  # noqa: ANN001
                self._queue.put(("log", f"[{event.phase}] {event.message}"))
                if event.phase == "step":
                    step = (event.data or {}).get("step") or {}
                    label = step.get("instruction") or step.get("action") or event.message
                    self._queue.put(("step", label))

            self._loop = AgentLoop(
                gemini=gemini,
                executor=executor,
                max_consecutive_failures=self.settings.max_consecutive_failures,
                replan_on_failure=self.settings.replan_on_failure,
                observe_max_candidates=self.settings.observe_max_candidates,
                on_event=on_event,
                confirm_callback=self._confirm_callback,
                collect_mode=collect_mode,
                app_dir=self.settings.app_dir,
                intent_sites_path=self.settings.intent_sites_path,
                intent_phrases_path=self.settings.intent_phrases_path,
                intent_default_result_type=self.settings.intent_default_result_type,
                extract_url_limit=self.settings.intent_extract_url_limit,
            )
            with build_session(self.settings, keep_open=self.settings.keep_browser_open) as session:
                self._queue.put(("log", LOGIN_GUIDE))
                page = get_active_page(session.browser)
                result = self._loop.run(page, instruction)
                self._queue.put(("done", result.to_dict()))
        except (BrowserError, GeminiError, Exception) as exc:  # noqa: BLE001
            logger.exception("UI worker failed")
            self._queue.put(("error", str(exc)))
        finally:
            self._loop = None


def run_ui(settings: Settings) -> int:
    root = tk.Tk()
    BrowserAssistantApp(root, settings)
    root.mainloop()
    return 0
