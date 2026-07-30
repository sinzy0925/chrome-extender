"""ログ出力の最低限セットアップ."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Literal, Sequence

from browser_assistant.paths import get_default_log_path


_CONFIGURED = False
_FILE_SESSION_STARTED = False


def _ensure_utf8_stdio() -> None:
    """Windows コンソール／リダイレクト時の文字化けを抑える."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _quote_arg(arg: str) -> str:
    if not arg:
        return '""'
    if any(c.isspace() for c in arg) or '"' in arg:
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


def format_launch_command(argv: Sequence[str] | None = None) -> str:
    """表示用の起動コマンド文字列を作る."""
    args = list(argv if argv is not None else sys.argv[1:])
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).name
        return " ".join([exe, *(_quote_arg(a) for a in args)])
    # 開発時: python -m browser_assistant ...
    return " ".join(["python", "-m", "browser_assistant", *(_quote_arg(a) for a in args)])


def describe_launch_mode(args: Any | None) -> str:
    """主要フラグから起動モードの短い説明を返す."""
    if args is None:
        return "(未解析)"
    run = (getattr(args, "run", "") or "").strip()
    if getattr(args, "ui", False):
        return "--ui"
    if run:
        bits = ["--run"]
        if getattr(args, "collect_mode", False):
            bits.append("--collect-mode")
        if getattr(args, "close_browser", False):
            bits.append("--close-browser")
        if getattr(args, "yes_danger", False):
            bits.append("--yes-danger")
        return " ".join(bits)
    if getattr(args, "startup_check", False):
        return "--startup-check"
    if getattr(args, "check_config", False):
        return "--check-config"
    if getattr(args, "start_browser", False):
        return "--start-browser"
    if getattr(args, "observe", False):
        return "--observe"
    if (getattr(args, "plan", "") or "").strip():
        return "--plan"
    if (getattr(args, "resolve_element", "") or "").strip():
        return "--resolve-element"
    if (getattr(args, "execute_json", "") or "").strip():
        return "--execute-json"
    return "(案内のみ)"


def log_launch_banner(
    logger: logging.Logger,
    *,
    argv: Sequence[str] | None = None,
    args: Any | None = None,
    app_dir: Path | None = None,
    version: str | None = None,
) -> None:
    """ログ先頭に起動方法を書き出す."""
    from browser_assistant.paths import get_app_dir

    raw_argv = list(argv if argv is not None else sys.argv)
    cmd = format_launch_command(raw_argv[1:] if raw_argv else [])
    mode = describe_launch_mode(args)
    base = app_dir if app_dir is not None else get_app_dir()

    logger.info("===== 起動 =====")
    logger.info("command: %s", cmd)
    logger.info("mode: %s", mode)
    if args is not None:
        run = (getattr(args, "run", "") or "").strip()
        if run:
            logger.info("instruction: %s", run)
        logger.info("collect_mode: %s", bool(getattr(args, "collect_mode", False)))
        logger.info("close_browser: %s", bool(getattr(args, "close_browser", False)))
        export_json = (getattr(args, "export_json", "") or "").strip()
        export_csv = (getattr(args, "export_csv", "") or "").strip()
        if export_json:
            logger.info("export_json: %s", export_json)
        if export_csv:
            logger.info("export_csv: %s", export_csv)
    logger.info("app_dir: %s", base)
    logger.info("cwd: %s", Path.cwd())
    logger.info("python: %s", sys.executable)
    logger.info("frozen: %s", bool(getattr(sys, "frozen", False)))
    if version:
        logger.info("version: %s", version)
    logger.info("argv: %s", raw_argv)
    logger.info("===============")


def setup_logging(
    level: str = "INFO",
    *,
    log_file: Path | None | Literal[False] = None,
    app_dir: Path | None = None,
) -> logging.Logger:
    """ルートロガーをコンソール＋ファイル向けに設定する.

    - ``log_file=None``: ``<app_dir>/log/browser_assistant.log``（毎回同じファイル名）
    - ``log_file=False``: コンソールのみ
    - ``log_file=Path``: 指定パス

    プロセス内の初回はファイルを上書き、以降の再設定は追記。
    ファイルは UTF-8（初回は BOM 付き）で書き、メモ帳でも文字化けしにくくする。
    """
    global _CONFIGURED, _FILE_SESSION_STARTED

    numeric = getattr(logging, level.upper(), None)
    if not isinstance(numeric, int):
        numeric = logging.INFO
        level = "INFO"

    _ensure_utf8_stdio()

    root = logging.getLogger()
    root.setLevel(numeric)

    # 再呼び出し時はハンドラを付け直す（テストでレベル変更を確認しやすくする）
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(numeric)
    console.setFormatter(formatter)
    root.addHandler(console)

    target: Path | None
    if log_file is False:
        target = None
    elif log_file is None:
        target = get_default_log_path(app_dir)
    else:
        target = Path(log_file)

    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        # プロセス初回は上書き＋BOM、以降は追記（再 setup で消さない）
        mode = "w" if not _FILE_SESSION_STARTED else "a"
        encoding = "utf-8-sig" if mode == "w" else "utf-8"
        file_handler = logging.FileHandler(target, mode=mode, encoding=encoding)
        file_handler.setLevel(numeric)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        _FILE_SESSION_STARTED = True

    _CONFIGURED = True
    logger = logging.getLogger("browser_assistant")
    logger.debug("logging configured: level=%s file=%s", level.upper(), target)
    return logger
