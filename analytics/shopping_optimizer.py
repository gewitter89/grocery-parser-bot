# -*- coding: utf-8 -*-
"""
analytics/shopping_optimizer.py — Оптимизатор списка покупок.
"""
import sqlite3
from matching.product_matcher import normalize_title

def optimize_shopping_list(db_path: str, items_list: list) -> dict:
    """
    Принимает текстовый список покупок пользователя.
    Ищет по БД и выдает оптимальный план покупок для минимизации расходов.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    result = {
        "by_store": {}, # store_id -> {"store_name": str, "items": [], "subtotal": float}
        "total": 0.0,
        "missing": []
    }
    
    try:
        # Для каждого пункта списка ищем товары
        for item_query in items_list:
            norm_query = normalize_title(item_query)
            if not norm_query:
                continue
                
            # Ищем товары с ценами за сегодня
            matches = conn.execute("""
                SELECT ph.price, ph.store_id, p.raw_title, s.name as store_name
                FROM price_history ph
                JOIN products p ON ph.product_id = p.id
                JOIN stores s ON ph.store_id = s.id
                WHERE ph.date = date('now') AND ph.in_stock = 1
                  AND (p.raw_title LIKE ? OR p.normalized_title LIKE ?)
                ORDER BY ph.price ASC
            """, (f"%{item_query}%", f"%{norm_query}%")).fetchall()
            
            if not matches:
                result["missing"].append(item_query)
                continue
                
            # Выбираем самый дешевый вариант
            best_match = matches[0]
            store_id = best_match["store_id"]
            store_name = best_match["store_name"]
            price = best_match["price"]
            title = best_match["raw_title"]
            
            if store_id not in result["by_store"]:
                result["by_store"][store_id] = {
                    "store_name": store_name,
                    "items": [],
                    "subtotal": 0.0
                }
                
            result["by_store"][store_id]["items"].append({
                "query": item_query,
                "title": title,
                "price": price
            })
            result["by_store"][store_id]["subtotal"] = round(result["by_store"][store_id]["subtotal"] + price, 2)
            result["total"] = round(result["total"] + price, 2)
            
        return result
    finally:
        conn.close()
