# -*- coding: utf-8 -*-
"""
monitoring/logger.py — Структурированное логирование в JSON.

Каждая строка лога — JSON-объект для удобного поиска и анализа:
{
  "ts": "2026-06-22T08:01:23",
  "level": "INFO",
  "module": "zakaz_scraper",
  "message": "Found 24 products for 'гречка'"
}
"""
import logging
import json
import os
import sys
from datetime import datetime, date
from logging.handlers import RotatingFileHandler

import config


class JSONFormatter(logging.Formatter):
    """Форматирует лог-записи в JSON."""

    def format(self, record):
        log_entry = {
            "ts": datetime.fromtimestamp(record.created).strftime("%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }

        # Добавляем extra-поля (если переданы через logger.info("msg", extra={...}))
        for key in ("store", "action", "query", "products_found",
                     "duration_ms", "status", "error"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        # Исключение
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Читаемый формат для консоли."""

    COLORS = {
        "DEBUG":    "\033[36m",    # cyan
        "INFO":     "\033[32m",    # green
        "WARNING":  "\033[33m",    # yellow
        "ERROR":    "\033[31m",    # red
        "CRITICAL": "\033[35m",    # magenta
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelname, "")
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        msg = record.getMessage()
        # Избавляемся от непечатаемых/некодируемых символов для Windows консоли
        msg = msg.encode('ascii', errors='replace').decode('ascii')
        return f"{color}{timestamp} [{record.levelname:7s}] {record.name}: {msg}{self.RESET}"


def setup_logging(level=logging.INFO, log_to_file=True, log_to_console=True):
    """
    Настроить логирование для всего приложения.
    
    Args:
        level: Уровень логирования
        log_to_file: Записывать в файл (JSON)
        log_to_console: Выводить в консоль (читаемый формат)
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Убрать существующие обработчики (если повторный вызов)
    root_logger.handlers.clear()

    # Файловый обработчик — JSON, ротируемый
    if log_to_file:
        log_file = os.path.join(
            config.LOG_DIR,
            f"pipeline_{date.today().isoformat()}.log"
        )
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,   # 10 МБ
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(JSONFormatter())
        file_handler.setLevel(level)
        root_logger.addHandler(file_handler)

    # Консольный обработчик — читаемый формат
    if log_to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(ConsoleFormatter())
        console_handler.setLevel(level)
        root_logger.addHandler(console_handler)

    # Заглушить слишком verbose библиотеки
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info("Logging configured: level=%s, file=%s, console=%s",
                logging.getLevelName(level), log_to_file, log_to_console)


def cleanup_old_logs(retention_days: int = None):
    """Удалить логи старше N дней."""
    if retention_days is None:
        retention_days = config.LOG_RETENTION_DAYS

    logger = logging.getLogger(__name__)
    cutoff = datetime.now().timestamp() - retention_days * 86400
    removed = 0

    if not os.path.exists(config.LOG_DIR):
        return

    for filename in os.listdir(config.LOG_DIR):
        filepath = os.path.join(config.LOG_DIR, filename)
        if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
            try:
                os.remove(filepath)
                removed += 1
            except OSError as e:
                logger.warning("Cannot remove old log %s: %s", filename, e)

    if removed:
        logger.info("Removed %d old log files (retention: %d days)",
                    removed, retention_days)
