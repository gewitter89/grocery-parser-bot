# -*- coding: utf-8 -*-
"""
analytics/trend_analyzer.py — Анализ временных рядов цен.

Рассчитывает скользящие средние (MA7, MA30, MA90), волатильность (std dev) и тренд.
"""
import sqlite3
import math
from datetime import date, timedelta

def get_product_price_stats(db_path: str, product_id: int, days: int = 90) -> dict:
    """
    Рассчитывает среднюю цену, минимальную цену, скользящие средние и волатильность.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Получаем историю цен
        rows = conn.execute("""
            SELECT price, date FROM price_history
            WHERE product_id = ? AND date >= date('now', ?)
            ORDER BY date DESC
        """, (product_id, f"-{days} days")).fetchall()
        
        if not rows:
            return {}
            
        prices = [r["price"] for r in rows]
        
        avg_price = sum(prices) / len(prices)
        min_price = min(prices)
        max_price = max(prices)
        
        # Стандартное отклонение (волатильность)
        variance = sum((p - avg_price) ** 2 for p in prices) / len(prices)
        std_dev = math.sqrt(variance)
        
        # Вычисляем скользящие средние
        ma7_prices = prices[:7]
        ma30_prices = prices[:30]
        
        ma7 = sum(ma7_prices) / len(ma7_prices) if ma7_prices else avg_price
        ma30 = sum(ma30_prices) / len(ma30_prices) if ma30_prices else avg_price
        
        # Направление тренда
        if len(prices) >= 7:
            if ma7 < ma30:
                trend = "falling"
            elif ma7 > ma30:
                trend = "rising"
            else:
                trend = "stable"
        else:
            trend = "stable"
            
        return {
            "avg_price": round(avg_price, 2),
            "min_price": min_price,
            "max_price": max_price,
            "std_dev": round(std_dev, 2),
            "ma7": round(ma7, 2),
            "ma30": round(ma30, 2),
            "trend": trend
        }
    finally:
        conn.close()


def predict_next_discount(db_path: str, product_id: int) -> dict:
    """
    Анализирует исторические циклы снижения цен (скидок) для товара
    и прогнозирует следующее вероятное окно скидки.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Извлекаем всю историю цен для нахождения минимальных пиков
        rows = conn.execute("""
            SELECT price, date FROM price_history
            WHERE product_id = ?
            ORDER BY date ASC
        """, (product_id,)).fetchall()

        if len(rows) < 14:  # Мало данных для анализа циклов
            return {"status": "insufficient_data"}

        prices = [r["price"] for r in rows]
        dates = [datetime.strptime(r["date"], "%Y-%m-%d") for r in rows]

        # Находим среднее за весь период для определения "скидочного" порога
        avg_price = sum(prices) / len(prices)
        
        # Скидочным днем считаем день, когда цена ниже средней на 10% и более
        discount_days = []
        for i, price in enumerate(prices):
            if price <= avg_price * 0.9:
                discount_days.append(dates[i])

        if len(discount_days) < 2:
            return {"status": "no_clear_cycles"}

        # Вычисляем интервалы между скидочными периодами (в днях)
        intervals = []
        last_date = discount_days[0]
        for d in discount_days[1:]:
            diff = (d - last_date).days
            # Считаем только интервалы больше 5 дней, чтобы пропустить один и тот же период скидок
            if diff > 5:
                intervals.append(diff)
                last_date = d

        if not intervals:
            return {"status": "no_clear_cycles"}

        avg_cycle_days = sum(intervals) / len(intervals)
        
        # Прогнозируем дату следующей скидки
        last_discount_date = discount_days[-1]
        next_predicted = last_discount_date + timedelta(days=round(avg_cycle_days))
        
        days_to_next = (next_predicted - datetime.now()).days

        return {
            "status": "success",
            "avg_cycle_days": round(avg_cycle_days, 1),
            "last_discount_date": last_discount_date.strftime("%Y-%m-%d"),
            "next_predicted_date": next_predicted.strftime("%Y-%m-%d"),
            "days_to_next": max(0, days_to_next)
        }
    finally:
        conn.close()
