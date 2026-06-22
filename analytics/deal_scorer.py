# -*- coding: utf-8 -*-
"""
analytics/deal_scorer.py — Оценка выгодности сделок (Deal Score).
"""
import sqlite3
import logging
from analytics.trend_analyzer import get_product_price_stats

logger = logging.getLogger(__name__)

def calculate_deal_score(db_path: str, product_id: int, current_price: float, current_store_id: str) -> dict:
    """
    Рассчитывает Deal Score от 0 до 100 для конкретного товара и его текущей цены.
    """
    stats = get_product_price_stats(db_path, product_id)
    if not stats:
        return {"score": 50, "reasons": ["Нет истории цен"]}
        
    avg = stats["avg_price"]
    p_min = stats["min_price"]
    trend = stats["trend"]
    
    # 1. Сравнение со средней за 90 дней (вес 30%)
    score_vs_avg = 50
    if avg > 0:
        diff_pct = (avg - current_price) / avg
        if diff_pct > 0:
            score_vs_avg = min(100, 50 + diff_pct * 150)
        else:
            score_vs_avg = max(0, 50 + diff_pct * 100)
            
    # 2. Сравнение с историческим минимумом (вес 25%)
    score_vs_min = 50
    if p_min > 0:
        if current_price <= p_min * 1.02:
            score_vs_min = 100
        else:
            diff_pct = (current_price - p_min) / p_min
            score_vs_min = max(0, 100 - diff_pct * 200)
            
    # 3. Кросс-магазинное сравнение (вес 20%)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    score_cross = 50
    reasons = []
    try:
        # Находим цены на этот же товар сегодня в других магазинах
        other_prices = conn.execute("""
            SELECT price, store_id FROM price_history
            WHERE product_id = ? AND date = date('now') AND store_id != ? AND in_stock = 1
        """, (product_id, current_store_id)).fetchall()
        
        if other_prices:
            min_other = min(r["price"] for r in other_prices)
            if current_price < min_other:
                score_cross = 100
                reasons.append("Самая дешевая цена среди всех магазинов сегодня")
            else:
                diff_pct = (current_price - min_other) / min_other
                score_cross = max(0, 80 - diff_pct * 200)
        else:
            score_cross = 70
    finally:
        conn.close()
        
    # 4. Направление тренда (вес 15%)
    score_trend = 50
    if trend == "falling":
        score_trend = 90
        reasons.append("Цена снижается последние недели")
    elif trend == "rising":
        score_trend = 20
        reasons.append("Цена растет")
        
    # 5. Волатильность (вес 10%) - если цена колеблется, ловить скидку важнее
    score_vol = 50
    if stats["std_dev"] > 0 and avg > 0:
        rel_vol = stats["std_dev"] / avg
        score_vol = min(100, rel_vol * 300)
        
    # Итоговый взвешенный Deal Score
    final_score = (
        score_vs_avg * 0.30 +
        score_vs_min * 0.25 +
        score_cross * 0.20 +
        score_trend * 0.15 +
        score_vol * 0.10
    )
    
    if avg > 0 and (avg - current_price) / avg > 0.15:
        reasons.append(f"Цена ниже средней за 90 дней на {round((avg - current_price)/avg * 100)}%")
    if current_price <= p_min * 1.02:
        reasons.append("Цена на уровне исторического минимума")
        
    return {
        "score": round(final_score),
        "reasons": reasons,
        "avg_price": avg,
        "min_price": p_min
    }

def get_top_deals(db_path: str, limit: int = 10) -> list:
    """
    Находит лучшие предложения (скидки) за сегодня на основе Deal Score.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    deals = []
    try:
        # Берем сегодняшние цены
        rows = conn.execute("""
            SELECT ph.product_id, ph.store_id, ph.price, ph.old_price, ph.link,
                   p.raw_title, p.normalized_title, p.category_id,
                   s.name as store_name
            FROM price_history ph
            JOIN products p ON ph.product_id = p.id
            JOIN stores s ON ph.store_id = s.id
            WHERE ph.date = date('now') AND ph.in_stock = 1
        """).fetchall()
        
        # Группируем цены по категориям для вычисления медиан (Outlier Detection)
        category_prices = {}
        for r in rows:
            cat = r["category_id"]
            if cat:
                if cat not in category_prices:
                    category_prices[cat] = []
                # Сохраняем цену за единицу
                price_per = r["price"] # дефолтная цена, если нет цены за кг
                category_prices[cat].append(price_per)

        cat_medians = {}
        for cat, prs in category_prices.items():
            sorted_prs = sorted(prs)
            n = len(sorted_prs)
            if n > 0:
                cat_medians[cat] = sorted_prs[n // 2]

        for r in rows:
            cat = r["category_id"]
            price = r["price"]
            
            # Проверка выбросов: если цена товара ниже медианы категории более чем в 5 раз,
            # вероятно это опечатка магазина (например, указана цена 10 грн вместо 100 грн).
            if cat in cat_medians and cat_medians[cat] > 0:
                if price < cat_medians[cat] * 0.20:
                    logger.warning("Ignored outlier product: %s, price: %.2f (category median: %.2f)",
                                   r["raw_title"], price, cat_medians[cat])
                    continue

            ds = calculate_deal_score(db_path, r["product_id"], price, r["store_id"])
            if ds["score"] >= 70:  # Порог хорошей сделки
                deals.append({
                    "product_id": r["product_id"],
                    "raw_title": r["raw_title"],
                    "store_name": r["store_name"],
                    "price": price,
                    "old_price": r["old_price"],
                    "link": r["link"],
                    "deal_score": ds["score"],
                    "reasons": ds["reasons"],
                    "avg_price": ds["avg_price"],
                    "min_price": ds["min_price"]
                })
        # Сортируем по убыванию Deal Score
        deals.sort(key=lambda x: x["deal_score"], reverse=True)
        return deals[:limit]
    finally:
        conn.close()
