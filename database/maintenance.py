# -*- coding: utf-8 -*-
"""
database/maintenance.py — Автоматическое обслуживание базы данных.

- Ежедневный бэкап
- VACUUM (сжатие и дефрагментация)
- Архивация данных старше 12 месяцев
- Очистка старых бэкапов
"""
import os
import shutil
import sqlite3
import logging
from datetime import date, datetime

import config

logger = logging.getLogger(__name__)


def backup_database(db_path: str = None, backup_dir: str = None):
    """
    Создать бэкап базы данных.
    
    Копирует файл в backups/grocery_prices_YYYY-MM-DD.db
    """
    if db_path is None:
        db_path = config.DB_PATH
    if backup_dir is None:
        backup_dir = config.BACKUP_DIR

    os.makedirs(backup_dir, exist_ok=True)

    today = date.today().isoformat()
    backup_name = f"grocery_prices_{today}.db"
    backup_path = os.path.join(backup_dir, backup_name)

    if os.path.exists(backup_path):
        logger.info("Backup already exists for today: %s", backup_name)
        return backup_path

    if not os.path.exists(db_path):
        logger.warning("Database file not found: %s", db_path)
        return None

    try:
        # Используем SQLite backup API для консистентности
        source = sqlite3.connect(db_path)
        dest = sqlite3.connect(backup_path)
        source.backup(dest)
        dest.close()
        source.close()

        size_mb = os.path.getsize(backup_path) / (1024 * 1024)
        logger.info("✅ Backup created: %s (%.1f MB)", backup_name, size_mb)
        return backup_path

    except Exception as e:
        logger.error("❌ Backup failed: %s", e)
        # Удалить неполный бэкап
        if os.path.exists(backup_path):
            os.remove(backup_path)
        return None


def cleanup_old_backups(backup_dir: str = None,
                         max_count: int = None):
    """
    Удалить старые бэкапы, оставив только последние N.
    """
    if backup_dir is None:
        backup_dir = config.BACKUP_DIR
    if max_count is None:
        max_count = config.MAX_BACKUP_COUNT

    if not os.path.exists(backup_dir):
        return

    # Собрать все файлы бэкапов
    backups = []
    for f in os.listdir(backup_dir):
        if f.startswith("grocery_prices_") and f.endswith(".db"):
            path = os.path.join(backup_dir, f)
            backups.append((path, os.path.getmtime(path)))

    # Отсортировать по дате (новые сначала)
    backups.sort(key=lambda x: x[1], reverse=True)

    # Удалить лишние
    removed = 0
    for path, _ in backups[max_count:]:
        try:
            os.remove(path)
            removed += 1
            logger.debug("Removed old backup: %s", os.path.basename(path))
        except OSError as e:
            logger.warning("Cannot remove backup %s: %s",
                          os.path.basename(path), e)

    if removed:
        logger.info("Cleaned up %d old backups (keeping %d)", removed, max_count)


def vacuum_database(db_path: str = None):
    """
    Выполнить VACUUM — сжатие и дефрагментация SQLite.
    
    VACUUM перестраивает весь файл БД, уменьшая его размер
    и повышая производительность чтения.
    """
    if db_path is None:
        db_path = config.DB_PATH

    if not os.path.exists(db_path):
        logger.warning("Database not found for VACUUM: %s", db_path)
        return

    size_before = os.path.getsize(db_path) / (1024 * 1024)

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("VACUUM")
        conn.close()

        size_after = os.path.getsize(db_path) / (1024 * 1024)
        saved = size_before - size_after

        logger.info("✅ VACUUM: %.1f MB → %.1f MB (saved %.1f MB)",
                    size_before, size_after, saved)
    except Exception as e:
        logger.error("❌ VACUUM failed: %s", e)


def archive_old_data(db_path: str = None, days: int = None):
    """
    Перенести данные старше N дней в архивную базу.
    
    Данные из price_history старше archive_after_days переносятся
    в grocery_prices_archive.db, а из основной базы удаляются.
    """
    if db_path is None:
        db_path = config.DB_PATH
    if days is None:
        days = config.ARCHIVE_AFTER_DAYS

    archive_path = os.path.join(os.path.dirname(db_path),
                                 "grocery_prices_archive.db")

    if not os.path.exists(db_path):
        return

    conn = sqlite3.connect(db_path)
    try:
        # Подсчитать строки для архивации
        cutoff = f"-{days} days"
        count = conn.execute(
            "SELECT COUNT(*) FROM price_history WHERE date < date('now', ?)",
            (cutoff,)
        ).fetchone()[0]

        if count == 0:
            logger.info("No data to archive (all data is within %d days)", days)
            return

        logger.info("Archiving %d price records older than %d days...",
                    count, days)

        # Создать/подключить архивную базу
        conn.execute(f"ATTACH DATABASE '{archive_path}' AS archive")

        # Создать таблицу в архиве (если нет)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS archive.price_history (
                id              INTEGER PRIMARY KEY,
                product_id      INTEGER NOT NULL,
                store_id        VARCHAR NOT NULL,
                date            DATE NOT NULL,
                price           REAL NOT NULL,
                old_price       REAL,
                price_per_unit  REAL,
                in_stock        BOOLEAN DEFAULT 1,
                link            TEXT
            )
        """)

        # Перенести данные
        conn.execute("""
            INSERT OR IGNORE INTO archive.price_history
            SELECT * FROM price_history
            WHERE date < date('now', ?)
        """, (cutoff,))

        # Удалить из основной базы
        conn.execute(
            "DELETE FROM price_history WHERE date < date('now', ?)",
            (cutoff,)
        )

        conn.execute("DETACH DATABASE archive")
        conn.commit()

        logger.info("✅ Archived %d records to %s", count,
                    os.path.basename(archive_path))

    except Exception as e:
        logger.error("❌ Archive failed: %s", e)
        conn.rollback()
    finally:
        conn.close()


def analyze_tables(db_path: str = None):
    """Запустить ANALYZE для обновления статистик оптимизатора запросов."""
    if db_path is None:
        db_path = config.DB_PATH

    if not os.path.exists(db_path):
        return

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("ANALYZE")
        conn.close()
        logger.info("✅ ANALYZE completed")
    except Exception as e:
        logger.error("❌ ANALYZE failed: %s", e)


def run_full_maintenance(db_path: str = None):
    """
    Выполнить полный цикл обслуживания:
      1. Бэкап
      2. Очистка старых бэкапов
      3. Архивация старых данных
      4. VACUUM
      5. ANALYZE
      6. Очистка старых логов
    """
    logger.info("🔧 Starting full maintenance...")

    backup_database(db_path)
    cleanup_old_backups()
    archive_old_data(db_path)
    vacuum_database(db_path)
    analyze_tables(db_path)

    from monitoring.logger import cleanup_old_logs
    cleanup_old_logs()

    logger.info("🔧 Full maintenance completed")
