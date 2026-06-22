# -*- coding: utf-8 -*-
"""
analytics/basket_tracker.py — Трекер потребительской корзины.
"""
import sqlite3
import json
from datetime import date
import config

def calculate_basket_today(db_path: str) -> dict:
    """
    Рассчитывает общую стоимость стандартной корзины из 25 товаров по каждому магазину за сегодня.
    Записывает снимок в базу данных.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    basket_items = config.BASKET_ITEMS
    stores_prices = {} # store_id -> {product_category: min_price}
    
    try:
        # Находим активные магазины
        stores = conn.execute("SELECT id, name FROM stores WHERE is_active = 1").fetchall()
        for s in stores:
            stores_prices[s["id"]] = {
                "name": s["name"],
                "items": {},
                "total": 0.0,
                "missing": []
            }
            
        # Загружаем сегодняшние цены по категориям корзины
        for category_id in basket_items:
            # Ищем самый дешевый товар этой категории в каждом магазине за сегодня
            prices = conn.execute("""
                SELECT ph.price, ph.store_id, p.raw_title
                FROM price_history ph
                JOIN products p ON ph.product_id = p.id
                WHERE p.category_id = ? AND ph.date = date('now') AND ph.in_stock = 1
                ORDER BY ph.price ASC
            """, (category_id,)).fetchall()
            
            # Магазины, у которых нашелся товар этой категории
            visited_stores = set()
            for p in prices:
                sid = p["store_id"]
                if sid in stores_prices and sid not in visited_stores:
                    stores_prices[sid]["items"][category_id] = {
                        "price": p["price"],
                        "title": p["raw_title"]
                    }
                    visited_stores.add(sid)
            
            # Если в магазине нет категории, пометим
            for sid, sdata in stores_prices.items():
                if category_id not in sdata["items"]:
                    sdata["missing"].append(category_id)
                    
        # Вычисляем сумму для каждого магазина.
        # Если не хватает товаров, делаем аппроксимацию (средняя цена по остальным магазинам для отсутствующего товара)
        category_averages = {}
        for category_id in basket_items:
            all_prices = []
            for sid, sdata in stores_prices.items():
                if category_id in sdata["items"]:
                    all_prices.append(sdata["items"][category_id]["price"])
            category_averages[category_id] = sum(all_prices) / len(all_prices) if all_prices else 0.0

        cheapest_store_id = None
        min_basket_cost = float("inf")
        
        for sid, sdata in stores_prices.items():
            total = 0.0
            for category_id in basket_items:
                if category_id in sdata["items"]:
                    total += sdata["items"][category_id]["price"]
                else:
                    # Корректировка: прибавляем среднюю цену отсутствующего товара
                    total += category_averages[category_id]
            sdata["total"] = round(total, 2)
            
            # Ищем самый дешевый (считаем только если отсутствующих категорий мало, например <= 5)
            if len(sdata["missing"]) <= 5 and total < min_basket_cost:
                min_basket_cost = total
                cheapest_store_id = sid
                
        # Сохраняем снимок в БД (для самого дешевого или в среднем)
        if cheapest_store_id and min_basket_cost != float("inf"):
            db_manager_save(conn, min_basket_cost, cheapest_store_id, stores_prices)
            
        return {
            "date": date.today().isoformat(),
            "stores": stores_prices,
            "cheapest_store": cheapest_store_id
        }
    finally:
        conn.close()

def db_manager_save(conn, basket_cost: float, cheapest_store: str, details: dict):
    conn.execute("""
        INSERT INTO basket_snapshots (date, basket_cost, cheapest_store, details_json)
        VALUES (date('now'), ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            basket_cost=excluded.basket_cost,
            cheapest_store=excluded.cheapest_store,
            details_json=excluded.details_json
    """, (basket_cost, cheapest_store, json.dumps(details, ensure_ascii=False)))
    conn.commit()
