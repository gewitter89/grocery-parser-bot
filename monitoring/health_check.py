# -*- coding: utf-8 -*-
"""
monitoring/health_check.py — Самодиагностика скраперов.

Перед каждым полным запуском:
  1. Проверяет доступность каждого API/сайта
  2. Сравнивает количество товаров с предыдущим запуском
  3. Если товаров упало > 50% — алерт
  4. Если скрапер не работает 3 запуска подряд — автоотключение
"""
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)


# URL-ы для проверки доступности (быстрый GET/HEAD)
HEALTH_ENDPOINTS = {
    "silpo":     "https://api.catalog.ecom.silpo.ua/api/2.0/exec/EcomCatalogGlobal",
    "fora":      "https://api.catalog.ecom.fora.ua/api/2.0/exec/EcomCatalogGlobal",
    "atb":       "https://www.atbmarket.com/",
    "novus":     "https://stores-api.zakaz.ua/stores/48201031/",
    "metro":     "https://stores-api.zakaz.ua/stores/48215610/",
    "auchan":    "https://stores-api.zakaz.ua/stores/48246401/",
    "ekomarket": "https://stores-api.zakaz.ua/stores/482800030/",
}


def check_endpoint(store_id: str, url: str, timeout: int = 10) -> dict:
    """
    Проверить доступность эндпоинта.
    
    Returns:
        {"store_id": str, "status": "ok"|"error", "response_ms": int, "error": str|None}
    """
    start = datetime.now()
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True,
                             headers={"User-Agent": "HealthCheck/1.0"})
        elapsed_ms = int((datetime.now() - start).total_seconds() * 1000)

        # POST endpoints return 405 for HEAD — that's fine, server is alive
        if resp.status_code in (200, 405, 301, 302):
            return {
                "store_id": store_id,
                "status": "ok",
                "response_ms": elapsed_ms,
                "http_code": resp.status_code,
                "error": None,
            }
        else:
            return {
                "store_id": store_id,
                "status": "error",
                "response_ms": elapsed_ms,
                "http_code": resp.status_code,
                "error": f"HTTP {resp.status_code}",
            }
    except requests.exceptions.Timeout:
        elapsed_ms = int((datetime.now() - start).total_seconds() * 1000)
        return {
            "store_id": store_id,
            "status": "error",
            "response_ms": elapsed_ms,
            "http_code": None,
            "error": "Timeout",
        }
    except requests.exceptions.ConnectionError as e:
        elapsed_ms = int((datetime.now() - start).total_seconds() * 1000)
        return {
            "store_id": store_id,
            "status": "error",
            "response_ms": elapsed_ms,
            "http_code": None,
            "error": f"Connection error: {str(e)[:100]}",
        }
    except Exception as e:
        elapsed_ms = int((datetime.now() - start).total_seconds() * 1000)
        return {
            "store_id": store_id,
            "status": "error",
            "response_ms": elapsed_ms,
            "http_code": None,
            "error": str(e)[:100],
        }


def run_health_checks() -> list:
    """
    Проверить все эндпоинты.
    
    Returns:
        Список dict с результатами проверок.
    """
    results = []
    for store_id, url in HEALTH_ENDPOINTS.items():
        result = check_endpoint(store_id, url)
        if result["status"] == "ok":
            logger.info("✅ %s: OK (%d ms)", store_id, result["response_ms"])
        else:
            logger.warning("❌ %s: %s (%d ms)", store_id,
                          result["error"], result["response_ms"])
        results.append(result)

    ok_count = sum(1 for r in results if r["status"] == "ok")
    total = len(results)
    logger.info("Health check: %d/%d stores OK", ok_count, total)

    return results


def check_product_count_drop(db_manager, store_id: str,
                              current_count: int,
                              drop_threshold: float = 0.5) -> dict | None:
    """
    Проверить, не упало ли количество товаров значительно.
    
    Args:
        db_manager: DatabaseManager instance
        store_id: ID магазина
        current_count: Сколько товаров найдено сейчас
        drop_threshold: Порог падения (0.5 = 50%)
        
    Returns:
        Словарь с алертом или None если всё нормально.
    """
    last_run = db_manager.get_last_scrape_run(store_id)
    if not last_run or last_run["status"] == "failed":
        return None

    prev_count = last_run.get("products_found", 0)
    if prev_count == 0:
        return None

    drop_ratio = 1 - (current_count / prev_count)
    if drop_ratio >= drop_threshold:
        alert = {
            "store_id": store_id,
            "type": "product_count_drop",
            "prev_count": prev_count,
            "current_count": current_count,
            "drop_percent": round(drop_ratio * 100, 1),
            "message": (
                f"⚠️ АЛЕРТ: {store_id} — найдено {current_count} товаров "
                f"(обычно ~{prev_count}). Падение на {round(drop_ratio*100)}%."
            ),
        }
        logger.warning(alert["message"])
        return alert

    return None


def count_consecutive_failures(db_manager, store_id: str,
                                max_check: int = 3) -> int:
    """
    Подсчитать количество последовательных неудачных запусков.
    
    Returns:
        Количество подряд идущих failed/partial запусков.
    """
    import sqlite3
    conn = sqlite3.connect(db_manager.db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT status FROM scrape_runs
            WHERE store_id = ?
            ORDER BY started_at DESC
            LIMIT ?
        """, (store_id, max_check)).fetchall()

        count = 0
        for row in rows:
            if row["status"] in ("failed", "partial"):
                count += 1
            else:
                break
        return count
    finally:
        conn.close()


def generate_system_status(db_manager) -> str:
    """
    Сгенерировать текстовый отчёт о статусе системы.
    
    Returns:
        Строка с отчётом для Telegram.
    """
    from datetime import date as dt_date

    lines = ["📊 *Статус системы*\n"]

    # Health check
    health = run_health_checks()
    ok = [r for r in health if r["status"] == "ok"]
    fail = [r for r in health if r["status"] != "ok"]

    lines.append(f"✅ Доступны: {len(ok)}/{len(health)} магазинов")
    if fail:
        for f in fail:
            lines.append(f"  ❌ {f['store_id']}: {f['error']}")

    # DB stats
    product_count = db_manager.get_product_count()
    price_count = db_manager.get_price_count()
    lines.append(f"\n📦 Товаров в базе: {product_count}")
    lines.append(f"💰 Цен за сегодня: {price_count}")

    # Last scrape runs
    lines.append("\n🕐 Последние запуски:")
    stores = db_manager.get_active_stores()
    for store in stores:
        last_run = db_manager.get_last_scrape_run(store["id"])
        if last_run:
            status_emoji = {"success": "✅", "partial": "⚠️", "failed": "❌",
                           "running": "🔄"}.get(last_run["status"], "❓")
            lines.append(
                f"  {status_emoji} {store['name']}: "
                f"{last_run['products_found']} товаров "
                f"({last_run.get('finished_at', 'N/A')[:16]})"
            )
        else:
            lines.append(f"  ⏳ {store['name']}: ещё не запускался")

    return "\n".join(lines)
