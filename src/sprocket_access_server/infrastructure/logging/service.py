from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import colorlog

MAX_LOG_BYTES = 5 * 1024 * 1024
MAX_LOG_FILES = 100
REDACTED = "<redacted>"
SENSITIVE_QUERY_KEYS = (
    "access_token",
    "authorization",
    "client_secret",
    "code",
    "key",
    "refresh_token",
    "session",
    "state",
    "token",
)
SENSITIVE_HEADER_NAMES = (
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-csrf-token",
    "x-xsrf-token",
)
SENSITIVE_QUERY_RE = re.compile(
    r"(?i)([?&;](?:" + "|".join(re.escape(key) for key in SENSITIVE_QUERY_KEYS) + r")=)([^&\s\"'<>]+)"
)
SENSITIVE_HEADER_RE = re.compile(
    r"(?im)^([^\S\r\n]*(?:<|>|\*)?\s*(?:"
    + "|".join(re.escape(name) for name in SENSITIVE_HEADER_NAMES)
    + r")\s*:\s*)([^\r\n]*)"
)
MANAGED_LOGGERS = (
    "sprocket_access_server",
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "starlette",
    "fastapi",
)


def configure_logging(level: str = "INFO", *, log_dir: Path | None = None) -> logging.Logger:
    """Configure one process-wide logger for infrastructure and modules."""

    normalized = level.strip().upper() or "INFO"
    log_level = getattr(logging, normalized, logging.INFO)
    redaction_filter = SensitiveDataFilter()

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()
    root_logger.setLevel(log_level)

    console = logging.StreamHandler(stream=sys.stdout)
    console.setLevel(log_level)
    console_formatter = "%(asctime)s - %(log_color)s%(levelname)s%(reset)s - %(name)s:%(lineno)d - %(message)s"
    console.setFormatter(colorlog.ColoredFormatter(
        console_formatter,
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red,bg_white',
        },
        reset=True,
        style='%'
    ))
    console.addFilter(redaction_filter)
    root_logger.addHandler(console)

    directory = (log_dir or Path.cwd() / "logs").expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    _remove_excess_logs(directory)

    file_handler = CappedRotatingFileHandler(
        directory / f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log",
        maxBytes=MAX_LOG_BYTES,
        backupCount=MAX_LOG_FILES - 1,
        encoding="utf-8",
        log_dir=directory,
    )
    file_handler.setLevel(log_level)
    file_formatter = "%(asctime)s - %(levelname)s - %(name)s:%(lineno)d - %(message)s"
    file_handler.setFormatter(logging.Formatter(file_formatter))
    file_handler.addFilter(redaction_filter)
    root_logger.addHandler(file_handler)

    for name in MANAGED_LOGGERS:
        managed = logging.getLogger(name)
        managed.handlers.clear()
        managed.setLevel(log_level)
        managed.propagate = True

    logger = logging.getLogger("sprocket_access_server")
    logger.debug("logging configured level=%s log_file=%s max_bytes=%d max_files=%d",
                 normalized, file_handler.baseFilename, MAX_LOG_BYTES, MAX_LOG_FILES)
    return logger


class CappedRotatingFileHandler(RotatingFileHandler):
    def __init__(self, filename: Path, *args, log_dir: Path, **kwargs) -> None:
        super().__init__(filename, *args, **kwargs)
        self._log_dir = log_dir

    def doRollover(self) -> None:
        super().doRollover()
        _remove_excess_logs(self._log_dir)


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _is_websocket_ping_pong(record):
            return False
        record.msg = _redact_value(record.msg)
        if isinstance(record.args, dict):
            record.args = {key: _redact_value(value) for key, value in record.args.items()}
        elif isinstance(record.args, tuple):
            record.args = tuple(_redact_value(value) for value in record.args)
        elif record.args:
            record.args = _redact_value(record.args)
        return True


def _redact_value(value: object) -> object:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, bytes):
        try:
            return _redact_text(value.decode("utf-8", errors="replace"))
        except Exception:
            return REDACTED
    return value


def _redact_text(text: str) -> str:
    text = SENSITIVE_QUERY_RE.sub(lambda match: f"{match.group(1)}{REDACTED}", text)
    return SENSITIVE_HEADER_RE.sub(lambda match: f"{match.group(1)}{REDACTED}", text)


def _is_websocket_ping_pong(record: logging.LogRecord) -> bool:
    if not (
            record.name.startswith("websockets")
            or record.name.startswith("uvicorn")
    ):
        return False
    try:
        message = record.getMessage().casefold()
    except Exception:
        return False
    return (
            "keepalive ping" in message
            or "keepalive pong" in message
            or message.lstrip().startswith(("< ping", "> ping", "< pong", "> pong"))
    )


def _remove_excess_logs(directory: Path) -> None:
    files = sorted(
        (path for path in directory.glob("*.log*") if path.is_file()),
        key=lambda path: (path.stat().st_mtime, path.name),
    )
    for path in files[:-MAX_LOG_FILES + 1]:
        try:
            path.unlink()
        except OSError:
            logging.getLogger("sprocket_access_server").warning("failed to remove old log file path=%s", path)
