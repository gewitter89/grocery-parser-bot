# -*- coding: utf-8 -*-
"""
database/db_manager.py — Управление SQLite базой данных.

Создание таблиц, индексов, CRUD-операции.
"""
import sqlite3
import json
import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Менеджер базы данных для хранения цен на продукты."""

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Создать соединение с включённым WAL и foreign keys."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Инициализация схемы базы данных."""
        conn = self._get_conn()
        try:
            conn.executescript("""
                -- Версия схемы
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                );

                -- Магазины
                CREATE TABLE IF NOT EXISTS stores (
                    id        VARCHAR PRIMARY KEY,
                    name      VARCHAR NOT NULL,
                    chain     VARCHAR,
                    is_active BOOLEAN DEFAULT 1
                );

                -- Категории товаров
                CREATE TABLE IF NOT EXISTS categories (
                    id              VARCHAR PRIMARY KEY,
                    category_group  VARCHAR NOT NULL,
                    name            VARCHAR NOT NULL,
                    search_keywords TEXT NOT NULL,
                    stock_up        BOOLEAN DEFAULT 0,
                    unit_type       VARCHAR DEFAULT 'kg'
                );

                -- Товары (уникальный = ean + store или normalized_title + weight + store)
                CREATE TABLE IF NOT EXISTS products (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id       VARCHAR REFERENCES categories(id),
                    ean               VARCHAR,
                    raw_title         VARCHAR NOT NULL,
                    normalized_title  VARCHAR,
                    brand             VARCHAR,
                    weight_g          REAL,
                    volume_ml         REAL,
                    piece_count       INTEGER,
                    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- Индексы для products
                CREATE INDEX IF NOT EXISTS idx_products_ean ON products(ean);
                CREATE INDEX IF NOT EXISTS idx_products_norm_title ON products(normalized_title);
                CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);

                -- История цен
                CREATE TABLE IF NOT EXISTS price_history (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id      INTEGER NOT NULL REFERENCES products(id),
                    store_id        VARCHAR NOT NULL REFERENCES stores(id),
                    date            DATE NOT NULL,
                    price           REAL NOT NULL,
                    old_price       REAL,
                    price_per_unit  REAL,
                    in_stock        BOOLEAN DEFAULT 1,
                    link            TEXT,
                    UNIQUE(product_id, store_id, date)
                );

                -- Индексы для price_history
                CREATE INDEX IF NOT EXISTS idx_ph_product_store ON price_history(product_id, store_id);
                CREATE INDEX IF NOT EXISTS idx_ph_date ON price_history(date);
                CREATE INDEX IF NOT EXISTS idx_ph_store_date ON price_history(store_id, date);

                -- Логи запусков скраперов
                CREATE TABLE IF NOT EXISTS scrape_runs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_id        VARCHAR NOT NULL,
                    started_at      TIMESTAMP NOT NULL,
                    finished_at     TIMESTAMP,
                    status          VARCHAR DEFAULT 'running',
                    products_found  INTEGER DEFAULT 0,
                    errors          TEXT
                );

                -- Watchlist пользователей
                CREATE TABLE IF NOT EXISTS user_watchlist (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     BIGINT NOT NULL,
                    product_id  INTEGER REFERENCES products(id),
                    target_price REAL,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, product_id)
                );

                -- Снимки стоимости корзины
                CREATE TABLE IF NOT EXISTS basket_snapshots (
                    date           DATE PRIMARY KEY,
                    basket_cost    REAL NOT NULL,
                    cheapest_store VARCHAR,
                    details_json   TEXT
                );
            """)

            # Вставить версию схемы, если её нет
            cur = conn.execute("SELECT COUNT(*) FROM schema_version")
            if cur.fetchone()[0] == 0:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)",
                             (self.SCHEMA_VERSION,))

            conn.commit()
            logger.info("Database initialized: %s", self.db_path)
        except Exception as e:
            logger.error("Failed to initialize database: %s", e)
            raise
        finally:
            conn.close()

    # ----------------------------------------------------------
    # Stores
    # ----------------------------------------------------------
    def upsert_store(self, store_id: str, name: str, chain: str = None):
        """Добавить или обновить магазин."""
        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT INTO stores (id, name, chain)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, chain=excluded.chain
            """, (store_id, name, chain))
            conn.commit()
        finally:
            conn.close()

    def get_active_stores(self) -> list:
        """Получить все активные магазины."""
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM stores WHERE is_active = 1").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ----------------------------------------------------------
    # Categories
    # ----------------------------------------------------------
    def upsert_category(self, cat_id: str, group: str, name: str,
                        keywords: list, stock_up: bool, unit: str):
        """Добавить или обновить категорию."""
        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT INTO categories (id, category_group, name, search_keywords, stock_up, unit_type)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    category_group=excluded.category_group,
                    name=excluded.name,
                    search_keywords=excluded.search_keywords,
                    stock_up=excluded.stock_up,
                    unit_type=excluded.unit_type
            """, (cat_id, group, name, ",".join(keywords), int(stock_up), unit))
            conn.commit()
        finally:
            conn.close()

    def sync_categories(self, categories_dict: dict):
        """Синхронизировать все категории из config.CATEGORIES."""
        conn = self._get_conn()
        try:
            for cat_id, info in categories_dict.items():
                conn.execute("""
                    INSERT INTO categories (id, category_group, name, search_keywords, stock_up, unit_type)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        category_group=excluded.category_group,
                        name=excluded.name,
                        search_keywords=excluded.search_keywords,
                        stock_up=excluded.stock_up,
                        unit_type=excluded.unit_type
                """, (
                    cat_id,
                    info["group"],
                    info["name"],
                    ",".join(info["keywords"]),
                    int(info["stock_up"]),
                    info["unit"],
                ))
            conn.commit()
            logger.info("Synced %d categories", len(categories_dict))
        finally:
            conn.close()

    # ----------------------------------------------------------
    # Products
    # ----------------------------------------------------------
    def find_or_create_product(self, ean: str, raw_title: str,
                               normalized_title: str, category_id: str = None,
                               brand: str = None, weight_g: float = None,
                               volume_ml: float = None, piece_count: int = None) -> int:
        """
        Найти существующий товар или создать новый.
        Стратегия поиска:
          1. По EAN (если есть) — точное совпадение
          2. По normalized_title + weight_g (если оба есть)
          3. По normalized_title (без веса) — если больше ничего нет
        Возвращает product_id.
        """
        conn = self._get_conn()
        try:
            product_id = None

            # 1. Поиск по EAN
            if ean:
                row = conn.execute(
                    "SELECT id FROM products WHERE ean = ?", (ean,)
                ).fetchone()
                if row:
                    product_id = row["id"]

            # 2. Поиск по нормализованному названию + вес
            if not product_id and normalized_title and weight_g:
                row = conn.execute(
                    "SELECT id FROM products WHERE normalized_title = ? AND weight_g = ?",
                    (normalized_title, weight_g)
                ).fetchone()
                if row:
                    product_id = row["id"]

            # 3. Поиск по нормализованному названию (без веса)
            if not product_id and normalized_title:
                row = conn.execute(
                    "SELECT id FROM products WHERE normalized_title = ? AND weight_g IS NULL",
                    (normalized_title,)
                ).fetchone()
                if row:
                    product_id = row["id"]

            # 4. Создать новый
            if not product_id:
                cur = conn.execute("""
                    INSERT INTO products (category_id, ean, raw_title, normalized_title,
                                          brand, weight_g, volume_ml, piece_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (category_id, ean, raw_title, normalized_title,
                      brand, weight_g, volume_ml, piece_count))
                conn.commit()
                product_id = cur.lastrowid
                logger.debug("Created new product #%d: %s", product_id, raw_title)
            else:
                # Обновить EAN если был пустой
                if ean:
                    conn.execute(
                        "UPDATE products SET ean = ? WHERE id = ? AND ean IS NULL",
                        (ean, product_id)
                    )
                # Обновить категорию если была пустая
                if category_id:
                    conn.execute(
                        "UPDATE products SET category_id = ? WHERE id = ? AND category_id IS NULL",
                        (category_id, product_id)
                    )
                conn.commit()

            return product_id
        finally:
            conn.close()

    # ----------------------------------------------------------
    # Price History
    # ----------------------------------------------------------
    def save_price(self, product_id: int, store_id: str, price: float,
                   old_price: float = None, price_per_unit: float = None,
                   in_stock: bool = True, link: str = None,
                   for_date: date = None):
        """Сохранить цену товара. Если цена уже есть за сегодня — обновить."""
        if for_date is None:
            for_date = date.today()

        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT INTO price_history
                    (product_id, store_id, date, price, old_price, price_per_unit, in_stock, link)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_id, store_id, date) DO UPDATE SET
                    price=excluded.price,
                    old_price=excluded.old_price,
                    price_per_unit=excluded.price_per_unit,
                    in_stock=excluded.in_stock,
                    link=excluded.link
            """, (product_id, store_id, for_date.isoformat(), price,
                  old_price, price_per_unit, int(in_stock), link))
            conn.commit()
        finally:
            conn.close()

    def get_price_history(self, product_id: int, store_id: str = None,
                          days: int = 90) -> list:
        """Получить историю цен за последние N дней."""
        conn = self._get_conn()
        try:
            if store_id:
                rows = conn.execute("""
                    SELECT * FROM price_history
                    WHERE product_id = ? AND store_id = ?
                      AND date >= date('now', ?)
                    ORDER BY date ASC
                """, (product_id, store_id, f"-{days} days")).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM price_history
                    WHERE product_id = ?
                      AND date >= date('now', ?)
                    ORDER BY date ASC
                """, (product_id, f"-{days} days")).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_today_prices(self, category_id: str = None,
                         for_date: date = None) -> list:
        """Получить все цены за сегодня (или указанную дату)."""
        if for_date is None:
            for_date = date.today()
        conn = self._get_conn()
        try:
            if category_id:
                rows = conn.execute("""
                    SELECT ph.*, p.raw_title, p.normalized_title, p.weight_g,
                           p.volume_ml, p.ean, p.category_id, p.brand,
                           s.name as store_name
                    FROM price_history ph
                    JOIN products p ON ph.product_id = p.id
                    JOIN stores s ON ph.store_id = s.id
                    WHERE ph.date = ? AND p.category_id = ?
                    ORDER BY ph.price_per_unit ASC
                """, (for_date.isoformat(), category_id)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT ph.*, p.raw_title, p.normalized_title, p.weight_g,
                           p.volume_ml, p.ean, p.category_id, p.brand,
                           s.name as store_name
                    FROM price_history ph
                    JOIN products p ON ph.product_id = p.id
                    JOIN stores s ON ph.store_id = s.id
                    WHERE ph.date = ?
                    ORDER BY p.category_id, ph.price_per_unit ASC
                """, (for_date.isoformat(),)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_avg_price(self, product_id: int, days: int = 90) -> float:
        """Средняя цена товара за N дней."""
        conn = self._get_conn()
        try:
            row = conn.execute("""
                SELECT AVG(price) as avg_price
                FROM price_history
                WHERE product_id = ? AND date >= date('now', ?)
            """, (product_id, f"-{days} days")).fetchone()
            return row["avg_price"] if row and row["avg_price"] else None
        finally:
            conn.close()

    def get_min_price(self, product_id: int, days: int = 365) -> float:
        """Минимальная цена товара за всю историю (или N дней)."""
        conn = self._get_conn()
        try:
            row = conn.execute("""
                SELECT MIN(price) as min_price
                FROM price_history
                WHERE product_id = ? AND date >= date('now', ?)
            """, (product_id, f"-{days} days")).fetchone()
            return row["min_price"] if row and row["min_price"] else None
        finally:
            conn.close()

    def get_cheapest_store_today(self, product_id: int,
                                 for_date: date = None) -> dict:
        """Найти самый дешёвый магазин для товара сегодня."""
        if for_date is None:
            for_date = date.today()
        conn = self._get_conn()
        try:
            row = conn.execute("""
                SELECT ph.*, s.name as store_name
                FROM price_history ph
                JOIN stores s ON ph.store_id = s.id
                WHERE ph.product_id = ? AND ph.date = ? AND ph.in_stock = 1
                ORDER BY ph.price ASC
                LIMIT 1
            """, (product_id, for_date.isoformat())).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ----------------------------------------------------------
    # Scrape Runs
    # ----------------------------------------------------------
    def start_scrape_run(self, store_id: str) -> int:
        """Зафиксировать начало запуска скрапера."""
        conn = self._get_conn()
        try:
            cur = conn.execute("""
                INSERT INTO scrape_runs (store_id, started_at, status)
                VALUES (?, ?, 'running')
            """, (store_id, datetime.now().isoformat()))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def finish_scrape_run(self, run_id: int, status: str,
                          products_found: int, errors: list = None):
        """Зафиксировать завершение запуска скрапера."""
        conn = self._get_conn()
        try:
            conn.execute("""
                UPDATE scrape_runs
                SET finished_at = ?, status = ?, products_found = ?, errors = ?
                WHERE id = ?
            """, (
                datetime.now().isoformat(),
                status,
                products_found,
                json.dumps(errors or [], ensure_ascii=False),
                run_id
            ))
            conn.commit()
        finally:
            conn.close()

    def get_last_scrape_run(self, store_id: str) -> dict:
        """Получить последний запуск скрапера для магазина."""
        conn = self._get_conn()
        try:
            row = conn.execute("""
                SELECT * FROM scrape_runs
                WHERE store_id = ?
                ORDER BY started_at DESC
                LIMIT 1
            """, (store_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ----------------------------------------------------------
    # Watchlist
    # ----------------------------------------------------------
    def add_to_watchlist(self, user_id: int, product_id: int,
                         target_price: float = None):
        """Добавить товар в вотчлист пользователя."""
        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT INTO user_watchlist (user_id, product_id, target_price)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, product_id) DO UPDATE SET target_price=excluded.target_price
            """, (user_id, product_id, target_price))
            conn.commit()
        finally:
            conn.close()

    def get_watchlist(self, user_id: int) -> list:
        """Получить вотчлист пользователя."""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT uw.*, p.raw_title, p.normalized_title, p.category_id
                FROM user_watchlist uw
                JOIN products p ON uw.product_id = p.id
                WHERE uw.user_id = ?
                ORDER BY uw.created_at ASC
            """, (user_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def remove_from_watchlist(self, user_id: int, product_id: int):
        """Удалить товар из вотчлиста."""
        conn = self._get_conn()
        try:
            conn.execute(
                "DELETE FROM user_watchlist WHERE user_id = ? AND product_id = ?",
                (user_id, product_id)
            )
            conn.commit()
        finally:
            conn.close()

    # ----------------------------------------------------------
    # Basket Snapshots
    # ----------------------------------------------------------
    def save_basket_snapshot(self, for_date: date, basket_cost: float,
                             cheapest_store: str, details: dict):
        """Сохранить снимок стоимости корзины."""
        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT INTO basket_snapshots (date, basket_cost, cheapest_store, details_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    basket_cost=excluded.basket_cost,
                    cheapest_store=excluded.cheapest_store,
                    details_json=excluded.details_json
            """, (
                for_date.isoformat(),
                basket_cost,
                cheapest_store,
                json.dumps(details, ensure_ascii=False),
            ))
            conn.commit()
        finally:
            conn.close()

    def get_basket_history(self, days: int = 30) -> list:
        """Получить историю корзины за последние N дней."""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT * FROM basket_snapshots
                WHERE date >= date('now', ?)
                ORDER BY date ASC
            """, (f"-{days} days",)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ----------------------------------------------------------
    # Utility / Stats
    # ----------------------------------------------------------
    def get_product_count(self) -> int:
        """Общее количество товаров."""
        conn = self._get_conn()
        try:
            return conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        finally:
            conn.close()

    def get_price_count(self, for_date: date = None) -> int:
        """Количество записей цен за дату."""
        if for_date is None:
            for_date = date.today()
        conn = self._get_conn()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM price_history WHERE date = ?",
                (for_date.isoformat(),)
            ).fetchone()[0]
        finally:
            conn.close()

    def search_products(self, query: str, limit: int = 20) -> list:
        """Поиск товаров по названию (LIKE)."""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT p.*, c.name as category_name
                FROM products p
                LEFT JOIN categories c ON p.category_id = c.id
                WHERE p.raw_title LIKE ? OR p.normalized_title LIKE ?
                LIMIT ?
            """, (f"%{query}%", f"%{query}%", limit)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_all_products_with_latest_price(self, category_id: str = None) -> list:
        """Получить все товары с последней ценой."""
        conn = self._get_conn()
        try:
            query = """
                SELECT p.id, p.raw_title, p.normalized_title, p.weight_g,
                       p.volume_ml, p.category_id, p.ean, p.brand,
                       ph.price, ph.old_price, ph.price_per_unit,
                       ph.store_id, ph.date, ph.link, ph.in_stock,
                       s.name as store_name
                FROM products p
                JOIN price_history ph ON ph.product_id = p.id
                JOIN stores s ON ph.store_id = s.id
                WHERE ph.date = (
                    SELECT MAX(ph2.date) FROM price_history ph2
                    WHERE ph2.product_id = p.id AND ph2.store_id = ph.store_id
                )
            """
            params = []
            if category_id:
                query += " AND p.category_id = ?"
                params.append(category_id)
            query += " ORDER BY p.category_id, ph.price_per_unit ASC"

            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
