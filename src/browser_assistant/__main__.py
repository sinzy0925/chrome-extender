"""エントリポイント."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from browser_assistant import __version__
from browser_assistant.agent import AgentLoop
from browser_assistant.browser import LOGIN_GUIDE, BrowserError, build_session
from browser_assistant.config import ConfigError, Settings, load_settings
from browser_assistant.executor import ActionError, ActionExecutor, ConfirmedAction
from browser_assistant.gemini_client import GeminiClient, GeminiError
from browser_assistant.logging_setup import setup_logging
from browser_assistant.observe import get_active_page, observe_page, save_observation_json
from browser_assistant.paths import get_app_dir, get_env_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="browser-assistant",
        description="日本語指示ブラウザ助手（MVP）",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="デスクトップUIを起動する",
    )
    parser.add_argument(
        "--startup-check",
        action="store_true",
        help="`.env` / APIキー / Chrome の起動チェックだけ行う",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="`.env` を読み、設定内容の要約を表示して終了する",
    )
    parser.add_argument(
        "--run",
        default="",
        help="日本語指示をエージェントループで実行する（分解→観察→確定→実行）",
    )
    parser.add_argument(
        "--collect-mode",
        action="store_true",
        help="収集モード（書き込み系 click/type/select を自動ブロック）",
    )
    parser.add_argument(
        "--export-json",
        default="",
        help="収集結果の JSON 保存先（--run 後）",
    )
    parser.add_argument(
        "--export-csv",
        default="",
        help="収集結果の CSV 保存先（UTF-8 BOM・--run 後）",
    )
    parser.add_argument(
        "--yes-danger",
        action="store_true",
        help="危険操作の確認をすべて『実行する』で自動承認（非推奨・テスト用）",
    )
    parser.add_argument(
        "--start-browser",
        action="store_true",
        help="CDP 付き Chrome を起動（または再利用）し、Playwright で接続確認する",
    )
    parser.add_argument(
        "--observe",
        action="store_true",
        help="現在ページ（または --url）を観察し、候補要素の要約JSONを出力する",
    )
    parser.add_argument(
        "--execute-json",
        default="",
        help="確定済みアクションJSON（1手）を実行する",
    )
    parser.add_argument(
        "--plan",
        default="",
        help="日本語指示を Flash でステップ分解する",
    )
    parser.add_argument(
        "--resolve-element",
        default="",
        help="1ステップ指示。--observation-json の候補から Lite で要素確定する",
    )
    parser.add_argument(
        "--observation-json",
        default="",
        help="要素確定に使う観察JSONファイル（--resolve-element 用）",
    )
    parser.add_argument(
        "--url",
        default="",
        help="観察/実行前に開く URL（--observe / --execute-json と併用）",
    )
    parser.add_argument(
        "--observe-out",
        default="",
        help="観察JSONの保存先パス（省略時は表示のみ）",
    )
    parser.add_argument(
        "--no-reobserve",
        action="store_true",
        help="実行後の再観察をスキップする",
    )
    parser.add_argument(
        "--close-browser",
        action="store_true",
        help="終了時にブラウザも閉じる（デフォルトはログイン維持のため残す）",
    )
    parser.add_argument(
        "--allow-empty-key",
        action="store_true",
        help="GEMINI_API_KEY が空でも設定読込を通す（診断用）",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _load_or_exit(app_dir, *, require_api_key: bool) -> Settings:
    try:
        return load_settings(app_dir=app_dir, require_api_key=require_api_key)
    except ConfigError as exc:
        setup_logging("ERROR")
        logging.getLogger("browser_assistant").error("%s", exc)
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


def _cmd_check_config(settings: Settings) -> int:
    setup_logging(settings.log_level)
    logger = logging.getLogger("browser_assistant")
    logger.info("設定を読み込みました")
    logger.info("app_dir=%s", settings.app_dir)
    logger.info("env_path=%s", settings.env_path)
    logger.info("log_level=%s", settings.log_level)
    logger.info("cdp=%s:%s", settings.cdp_host, settings.cdp_port)
    logger.info("user_data_dir=%s", settings.user_data_dir)
    logger.info("keep_browser_open=%s", settings.keep_browser_open)
    logger.info("chrome_path=%s", settings.chrome_path or "(auto)")
    logger.info("observe_max_candidates=%s", settings.observe_max_candidates)
    logger.info("max_steps=%s", settings.max_steps)
    logger.info("post_action_wait_ms=%s", settings.post_action_wait_ms)
    logger.info("model_flash=%s", settings.gemini_model_flash)
    logger.info("model_flash_lite=%s", settings.gemini_model_flash_lite)
    logger.info(
        "gemini_api_key=%s",
        ("*" * 8 + settings.gemini_api_key[-4:])
        if len(settings.gemini_api_key) >= 4
        else "(empty)",
    )
    try:
        import google.genai  # noqa: F401

        logger.info("google-genai SDK: import OK")
    except ImportError:
        logger.error(
            "google-genai がインストールされていません。requirements.txt を確認してください。"
        )
        return 1

    print("設定OK")
    return 0


def _cmd_start_browser(settings: Settings, *, close_browser: bool) -> int:
    setup_logging(settings.log_level)
    logger = logging.getLogger("browser_assistant")
    keep_open = settings.keep_browser_open and not close_browser

    try:
        with build_session(settings, keep_open=keep_open) as session:
            print(LOGIN_GUIDE)
            page_urls: list[str] = []
            for ctx in session.browser.contexts:
                for page in ctx.pages:
                    page_urls.append(page.url)
            logger.info("開いているタブ: %s", page_urls or ["(なし)"])
            print(f"CDP接続OK: {session.cdp_url}")
            print(f"user-data-dir: {settings.user_data_dir}")
            if keep_open:
                print("終了方針: ブラウザは残します（再接続・ログイン維持用）")
            else:
                print("終了方針: ブラウザを閉じます")
    except BrowserError as exc:
        logger.error("%s", exc)
        print(str(exc), file=sys.stderr)
        return 1

    return 0


def _cmd_observe(
    settings: Settings,
    *,
    url: str,
    out_path: str,
    close_browser: bool,
) -> int:
    setup_logging(settings.log_level)
    logger = logging.getLogger("browser_assistant")
    keep_open = settings.keep_browser_open and not close_browser

    try:
        with build_session(settings, keep_open=keep_open) as session:
            print(LOGIN_GUIDE)
            page = get_active_page(session.browser)
            if url:
                logger.info("URL を開きます: %s", url)
                page.goto(url, wait_until="domcontentloaded")
            observation = observe_page(
                page,
                max_candidates=settings.observe_max_candidates,
            )
            print(observation.to_json())
            if out_path:
                saved = save_observation_json(observation, Path(out_path))
                logger.info("観察結果を保存しました: %s", saved)
                print(f"saved: {saved}")
    except BrowserError as exc:
        logger.error("%s", exc)
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        logger.exception("観察に失敗しました")
        print(f"観察に失敗しました: {exc}", file=sys.stderr)
        return 1

    return 0


def _cmd_plan(settings: Settings, instruction: str) -> int:
    setup_logging(settings.log_level)
    logger = logging.getLogger("browser_assistant")
    try:
        client = GeminiClient(settings)
        plan = client.plan_steps(instruction)
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    except GeminiError as exc:
        logger.error("%s", exc)
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _cmd_resolve_element(
    settings: Settings,
    *,
    step_instruction: str,
    observation_json: str,
) -> int:
    setup_logging(settings.log_level)
    logger = logging.getLogger("browser_assistant")
    path = Path(observation_json)
    if not path.is_file():
        print(f"観察JSONが見つかりません: {path}", file=sys.stderr)
        return 1
    try:
        obs = json.loads(path.read_text(encoding="utf-8"))
        client = GeminiClient(settings)
        resolved = client.resolve_element(step_instruction, obs)
        print(json.dumps(resolved.to_dict(), ensure_ascii=False, indent=2))
    except (OSError, json.JSONDecodeError, GeminiError) as exc:
        logger.error("%s", exc)
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _cmd_execute(
    settings: Settings,
    *,
    action_json_path: str,
    url: str,
    close_browser: bool,
    reobserve: bool,
) -> int:
    setup_logging(settings.log_level)
    logger = logging.getLogger("browser_assistant")
    path = Path(action_json_path)
    if not path.is_file():
        print(f"アクションJSONが見つかりません: {path}", file=sys.stderr)
        return 1
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        action = ConfirmedAction.from_dict(payload)
    except (OSError, json.JSONDecodeError, ActionError) as exc:
        print(f"アクションJSONが不正です: {exc}", file=sys.stderr)
        return 1

    keep_open = settings.keep_browser_open and not close_browser
    try:
        with build_session(settings, keep_open=keep_open) as session:
            print(LOGIN_GUIDE)
            page = get_active_page(session.browser)
            if url:
                page.goto(url, wait_until="domcontentloaded")
            executor = ActionExecutor(
                max_steps=settings.max_steps,
                post_wait_ms=settings.post_action_wait_ms,
                reobserve=reobserve,
                max_candidates=settings.observe_max_candidates,
            )
            result = executor.run_one(page, action)
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    except (BrowserError, ActionError) as exc:
        logger.error("%s", exc)
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _cmd_run(
    settings: Settings,
    *,
    instruction: str,
    close_browser: bool,
    collect_mode: bool,
    auto_approve_danger: bool,
    export_json: str = "",
    export_csv: str = "",
) -> int:
    import signal

    from browser_assistant.export import CollectionStore, ExportError, export_collection
    from browser_assistant.safety import ConfirmDecision, console_confirm

    setup_logging(settings.log_level)
    logger = logging.getLogger("browser_assistant")
    keep_open = settings.keep_browser_open and not close_browser
    use_collect = settings.collect_mode or collect_mode

    def _confirm(action, verdict):
        if auto_approve_danger:
            print(f"[safety] 自動承認: {verdict.summary}")
            return ConfirmDecision.EXECUTE
        return console_confirm(action, verdict)

    gemini = GeminiClient(settings)
    executor = ActionExecutor(
        max_steps=settings.max_steps,
        post_wait_ms=settings.post_action_wait_ms,
        reobserve=True,
        max_candidates=settings.observe_max_candidates,
    )
    loop = AgentLoop(
        gemini=gemini,
        executor=executor,
        max_consecutive_failures=settings.max_consecutive_failures,
        replan_on_failure=settings.replan_on_failure,
        observe_max_candidates=settings.observe_max_candidates,
        on_event=lambda e: print(f"[{e.phase}] {e.message}"),
        confirm_callback=_confirm,
        collect_mode=use_collect,
    )

    def _handle_sigint(signum, frame):  # noqa: ANN001, ARG001
        print("\nStop を受け付けました。現在のステップの後で停止します…", file=sys.stderr)
        loop.request_stop()

    previous = signal.signal(signal.SIGINT, _handle_sigint)
    try:
        with build_session(settings, keep_open=keep_open) as session:
            print(LOGIN_GUIDE)
            if use_collect:
                print("収集モード: 書き込み系操作はブロックされます")
            print("中断: Ctrl+C / 危険操作時は e=実行 s=スキップ a=中止")
            page = get_active_page(session.browser)
            result = loop.run(page, instruction)
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))

            store = CollectionStore()
            store.extend(result.extracts)
            print("\n=== 収集プレビュー ===")
            print(store.preview())

            if export_json or export_csv:
                try:
                    saved = export_collection(
                        store,
                        json_path=export_json or None,
                        csv_path=export_csv or None,
                    )
                    for kind, path in saved.items():
                        print(f"saved {kind}: {path}")
                except ExportError as exc:
                    logger.warning("%s", exc)
                    print(str(exc), file=sys.stderr)
            elif store.is_empty:
                print("（収集0件のためエクスポートはスキップ。--export-json / --export-csv で保存可）")

            return 0 if result.status in {"completed", "ask_user", "stopped"} else 1
    except (BrowserError, GeminiError) as exc:
        logger.error("%s", exc)
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGINT, previous)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app_dir = get_app_dir()
    logger = setup_logging("INFO")

    plan_text = (args.plan or "").strip()
    resolve_text = (args.resolve_element or "").strip()
    execute_path = (args.execute_json or "").strip()
    run_text = (args.run or "").strip()
    any_cmd = (
        args.check_config
        or args.start_browser
        or args.observe
        or bool(plan_text)
        or bool(resolve_text)
        or bool(execute_path)
        or bool(run_text)
        or args.ui
        or args.startup_check
    )
    if not any_cmd:
        # 案内 + 起動チェック（落ちない）
        from browser_assistant.startup_check import format_startup_report, run_startup_checks

        logger.info(
            "MVP 実装済みです。"
            " `--ui` / `--run` / `--startup-check` / `--check-config` などを使ってください。"
        )
        logger.info("アプリディレクトリ: %s", app_dir)
        logger.info(".env 想定パス: %s", get_env_path(app_dir))
        print(format_startup_report(run_startup_checks(app_dir=app_dir, require_api_key=False)))
        return 0

    if resolve_text and not args.observation_json.strip():
        print("--resolve-element には --observation-json が必要です", file=sys.stderr)
        return 1

    if args.startup_check:
        from browser_assistant.startup_check import (
            format_startup_report,
            run_startup_checks,
            startup_ok,
        )

        items = run_startup_checks(app_dir=app_dir, require_api_key=True)
        print(format_startup_report(items))
        return 0 if startup_ok(items) else 1

    needs_key = bool(
        plan_text
        or resolve_text
        or run_text
        or args.ui
        or (args.check_config and not args.allow_empty_key)
    )
    browser_cmds = args.start_browser or args.observe or bool(execute_path)

    if browser_cmds and not (plan_text or resolve_text or run_text or args.ui or args.check_config):
        settings = _load_or_exit(app_dir, require_api_key=False)
        if execute_path:
            return _cmd_execute(
                settings,
                action_json_path=execute_path,
                url=args.url.strip(),
                close_browser=args.close_browser,
                reobserve=not args.no_reobserve,
            )
        if args.observe:
            return _cmd_observe(
                settings,
                url=args.url.strip(),
                out_path=args.observe_out.strip(),
                close_browser=args.close_browser,
            )
        return _cmd_start_browser(settings, close_browser=args.close_browser)

    settings = _load_or_exit(
        app_dir,
        require_api_key=needs_key and not args.allow_empty_key,
    )

    if args.check_config:
        code = _cmd_check_config(settings)
        if code != 0:
            return code
        if not (
            args.start_browser
            or args.observe
            or plan_text
            or resolve_text
            or execute_path
            or run_text
            or args.ui
        ):
            return 0

    if args.ui:
        from browser_assistant.startup_check import format_startup_report, run_startup_checks
        from browser_assistant.ui_app import run_ui

        items = run_startup_checks(app_dir=app_dir, require_api_key=True)
        print(format_startup_report(items))
        bad = [i for i in items if not i.ok]
        if bad:
            print("起動チェックに問題があります。UIは開きますが、実行前に修正してください。")
        return run_ui(settings)
    if run_text:
        return _cmd_run(
            settings,
            instruction=run_text,
            close_browser=args.close_browser,
            collect_mode=args.collect_mode,
            auto_approve_danger=args.yes_danger,
            export_json=args.export_json.strip(),
            export_csv=args.export_csv.strip(),
        )
    if plan_text:
        return _cmd_plan(settings, plan_text)
    if resolve_text:
        return _cmd_resolve_element(
            settings,
            step_instruction=resolve_text,
            observation_json=args.observation_json.strip(),
        )
    if execute_path:
        return _cmd_execute(
            settings,
            action_json_path=execute_path,
            url=args.url.strip(),
            close_browser=args.close_browser,
            reobserve=not args.no_reobserve,
        )
    if args.observe:
        return _cmd_observe(
            settings,
            url=args.url.strip(),
            out_path=args.observe_out.strip(),
            close_browser=args.close_browser,
        )
    if args.start_browser:
        return _cmd_start_browser(settings, close_browser=args.close_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
