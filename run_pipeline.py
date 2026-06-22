# -*- coding: utf-8 -*-
"""
run_pipeline.py — Главный оркестратор системы мониторинга цен.

Режимы запуска:
  python run_pipeline.py                   # Полный цикл: парсинг + анализ + отчёт
  python run_pipeline.py --scrape          # Только парсинг
  python run_pipeline.py --report          # Только отчёт
  python run_pipeline.py --maintenance     # Только обслуживание БД
  python run_pipeline.py --status          # Проверка здоровья
  python run_pipeline.py --init            # Инициализация (создать БД, синхронить категории)
"""
import argparse
import logging
import sys
import time
from datetime import datetime, date

import config
from monitoring.logger import setup_logging
from database.db_manager import DatabaseManager
from matching.weight_parser import parse_weight, parse_volume, parse_piece_count, calculate_price_per_unit
from matching.product_matcher import normalize_title, extract_brand

logger = logging.getLogger("pipeline")


def initialize(db: DatabaseManager):
    """Инициализация: синхронизировать магазины и категории."""
    logger.info("📋 Initializing stores and categories...")

    # Синхронизировать магазины
    for store_id, store_name in config.STORES.items():
        chain = store_id  # для простых магазинов chain = id
        db.upsert_store(store_id, store_name, chain)

    # Zakaz.ua магазины — добавить все варианты
    for zstore_id, info in config.ZAKAZ_STORES.items():
        db.upsert_store(info["chain"], info["name"], info["chain"])

    # Синхронизировать категории
    db.sync_categories(config.CATEGORIES)

    logger.info("✅ Initialized %d stores, %d categories",
                len(config.STORES), len(config.CATEGORIES))


def run_scraping(db: DatabaseManager):
    """
    Запустить парсинг всех магазинов.
    
    Для каждого магазина:
      1. Health check
      2. Собрать товары по всем категориям
      3. Нормализовать названия, парсить вес/объём
      4. Сохранить в БД
      5. Залогировать результат
    """
    from scrapers import ALL_SCRAPERS
    from monitoring.health_check import check_product_count_drop

    logger.info("🔍 Starting scraping pipeline...")
    total_products = 0
    alerts = []

    # Собрать ключевые слова: {"grechka": "гречка", ...}
    # Используем первое ключевое слово из каждой категории
    keywords = {}
    for cat_id, cat_info in config.CATEGORIES.items():
        keywords[cat_id] = cat_info["keywords"][0]

    for scraper_name, scraper_factory in ALL_SCRAPERS.items():
        run_id = db.start_scrape_run(scraper_name)
        errors = []
        products_found = 0

        try:
            logger.info("🏪 Scraping %s...", scraper_name)
            start_time = time.time()

            scraper = scraper_factory()
            raw_products = scraper.scrape(keywords)
            scraper.close()

            elapsed = time.time() - start_time
            logger.info("  → %s returned %d raw products in %.1f sec",
                        scraper_name, len(raw_products), elapsed)

            # Обработать каждый товар
            for raw in raw_products:
                try:
                    product_data = _process_raw_product(raw, db)
                    if product_data:
                        products_found += 1
                except Exception as e:
                    err_msg = f"Error processing product '{raw.get('raw_title', '?')}': {e}"
                    errors.append(err_msg)
                    logger.debug(err_msg)

            # Проверить падение количества товаров
            alert = check_product_count_drop(db, scraper_name, products_found)
            if alert:
                alerts.append(alert)

            status = "success" if not errors else "partial"
            total_products += products_found

        except Exception as e:
            status = "failed"
            errors.append(str(e))
            logger.error("❌ Scraper %s failed: %s", scraper_name, e)

        db.finish_scrape_run(run_id, status, products_found, errors[:50])
        logger.info("  ✅ %s: %d products saved, status=%s",
                    scraper_name, products_found, status)

    logger.info("🏁 Scraping complete: %d total products", total_products)
    return {"total_products": total_products, "alerts": alerts}


def _process_raw_product(raw: dict, db: DatabaseManager) -> dict | None:
    """
    Обработать один сырой продукт от скрапера.
    
    1. Нормализовать название
    2. Парсить вес/объём/количество
    3. Найти или создать product в БД
    4. Рассчитать price_per_unit
    5. Сохранить price_history
    """
    raw_title = raw.get("raw_title", "").strip()
    if not raw_title:
        return None

    price = raw.get("price")
    if not price or price <= 0:
        return None

    # Парсим вес/объём/количество
    weight_g = raw.get("weight_g") or parse_weight(raw_title)
    volume_ml = raw.get("volume_ml") or parse_volume(raw_title)
    piece_count = parse_piece_count(raw_title)

    # Нормализуем название
    norm_title = normalize_title(raw_title)
    brand = extract_brand(raw_title)
    ean = raw.get("ean")

    # Определяем категорию
    category_id = raw.get("category_hint")

    # Определяем unit_type
    unit_type = "kg"
    if category_id and category_id in config.CATEGORIES:
        unit_type = config.CATEGORIES[category_id].get("unit", "kg")

    # Рассчитываем price_per_unit
    price_per_unit = calculate_price_per_unit(
        price, weight_g, volume_ml, piece_count, unit_type
    )

    # Определяем store_id
    store_id = raw.get("store_id", "unknown")
    # Для zakaz.ua — маппим числовые ID на названия цепочек
    if store_id in config.ZAKAZ_STORES:
        store_id = config.ZAKAZ_STORES[store_id]["chain"]

    # Находим или создаём товар в БД
    product_id = db.find_or_create_product(
        ean=ean,
        raw_title=raw_title,
        normalized_title=norm_title,
        category_id=category_id,
        brand=brand,
        weight_g=weight_g,
        volume_ml=volume_ml,
        piece_count=piece_count,
    )

    # Сохраняем цену
    db.save_price(
        product_id=product_id,
        store_id=store_id,
        price=price,
        old_price=raw.get("old_price"),
        price_per_unit=price_per_unit,
        in_stock=raw.get("in_stock", True),
        link=raw.get("link", ""),
    )

    return {
        "product_id": product_id,
        "store_id": store_id,
        "price": price,
        "price_per_unit": price_per_unit,
    }


def run_report(db: DatabaseManager):
    """
    Сгенерировать и отправить один компактный дайджест лучших скидок дня в Telegram.
    Использует HTML разметку для красивого отображения зачеркнутых цен на телефонах.
    """
    from analytics.deal_scorer import get_top_deals
    from telegram_bot.bot import send_telegram_message
    import sqlite3

    logger.info("📊 Generating daily report...")

    try:
        deals = get_top_deals(db.db_path, limit=15) # Берем больше сделок для фильтрации
        if deals:
            conn = sqlite3.connect(db.db_path)
            conn.row_factory = sqlite3.Row
            
            digest_lines = ["⚡️ <b>ТОП СКИДОК ДНЯ!</b>\n"]
            added_count = 0
            
            for d in deals:
                if added_count >= 5: # Выводим топ-5 в одном сообщении
                    break

                # Рассчитываем скидку
                pct = 0
                if d["old_price"] and d["old_price"] > d["price"]:
                    pct = round((d["old_price"] - d["price"]) / d["old_price"] * 100)
                elif d["avg_price"] and d["avg_price"] > d["price"]:
                    pct = round((d["avg_price"] - d["price"]) / d["avg_price"] * 100)

                if pct < 10:
                    continue  # Пропускаем мелкие скидки

                # Эмодзи: красный круг для 25%+, огонь для остальных
                emoji = "🔴" if pct >= 25 else "🔥"
                
                old_price_val = d["old_price"] or d["avg_price"]
                old_price_str = f" <s>{round(old_price_val, 1)}</s>"
                
                # Формируем компактную строчку на HTML:
                # 🔴 <b>-34%</b> <a href="ссылка">Название</a> — <b>20.7 грн</b> <s>20.7</s> | Novus
                line = f"{emoji} <b>-{pct}%</b> <a href=\"{d['link']}\">{d['raw_title']}</a> — <b>{d['price']} грн</b>{old_price_str} ({d['store_name']})"
                digest_lines.append(line)
                added_count += 1
                
            conn.close()
            
            if added_count > 0:
                # Отправляем ровно ОДНО сообщение, используя HTML режим
                token = config.TELEGRAM_BOT_TOKEN
                cid = config.TELEGRAM_CHAT_ID
                if token and cid:
                    import requests
                    url = f"https://api.telegram.org/bot{token}/sendMessage"
                    payload = {
                        "chat_id": cid,
                        "text": "\n".join(digest_lines),
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True
                    }
                    requests.post(url, json=payload, timeout=10)
                logger.info("📨 Compact HTML deals digest sent successfully")
            else:
                logger.info("No hot deals found for digest today")
        else:
            logger.info("No deals to report today")
    except Exception as e:
        logger.error("Failed to generate deals report: %s", e)


def run_maintenance():
    """Выполнить обслуживание БД."""
    from database.maintenance import run_full_maintenance
    run_full_maintenance()


def run_status(db: DatabaseManager):
    """Проверить статус системы и отправить в Telegram."""
    from monitoring.health_check import generate_system_status
    from telegram_bot.bot import send_telegram_message

    status_text = generate_system_status(db)
    print(status_text)

    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        send_telegram_message(status_text)


def main():
    parser = argparse.ArgumentParser(
        description="Grocery Price Intelligence System — Pipeline"
    )
    parser.add_argument("--scrape", action="store_true",
                        help="Только парсинг")
    parser.add_argument("--report", action="store_true",
                        help="Только отчёт")
    parser.add_argument("--maintenance", action="store_true",
                        help="Только обслуживание БД")
    parser.add_argument("--status", action="store_true",
                        help="Проверка здоровья")
    parser.add_argument("--init", action="store_true",
                        help="Инициализация (создать БД + категории)")
    parser.add_argument("--debug", action="store_true",
                        help="Включить отладочное логирование")

    args = parser.parse_args()

    # Настроить логирование
    level = logging.DEBUG if args.debug else logging.INFO
    setup_logging(level=level)

    # Инициализация БД
    db = DatabaseManager(config.DB_PATH)
    initialize(db)

    start_time = time.time()

    try:
        if args.init:
            logger.info("✅ Initialization complete")
            return

        if args.maintenance:
            run_maintenance()
            return

        if args.status:
            run_status(db)
            return

        if args.scrape:
            result = run_scraping(db)
            # Отправить алерты если есть
            if result.get("alerts"):
                from telegram_bot.bot import send_telegram_message
                for alert in result["alerts"]:
                    send_telegram_message(alert["message"])
            return

        if args.report:
            run_report(db)
            return

        # Без аргументов — полный цикл
        logger.info("🚀 Starting full pipeline...")

        # 1. Парсинг
        result = run_scraping(db)

        # 2. Отчёт
        run_report(db)

        # 3. Алерты
        if result.get("alerts"):
            from telegram_bot.bot import send_telegram_message
            for alert in result["alerts"]:
                send_telegram_message(alert["message"])

        elapsed = time.time() - start_time
        logger.info("🏁 Full pipeline completed in %.1f sec", elapsed)

    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
    except Exception as e:
        logger.error("💥 Pipeline failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
