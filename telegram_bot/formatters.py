# -*- coding: utf-8 -*-
"""
telegram_bot/formatters.py — Форматирование сообщений.
"""

def format_deals_report(deals: list) -> str:
    """Хайповый современный дизайн лучших скидок в стиле Трухи."""
    if not deals:
        return "🤷‍♂️ *Сегодня без жестких скидок.*"
        
    lines = ["🔥 *ТОП САМЫХ ВЫГОДНЫХ СКИДОК НА СЕГОДНЯ!* \n"]
    
    for d in deals[:10]:
        pct = 0
        if d["old_price"] and d["old_price"] > d["price"]:
            pct = round((d["old_price"] - d["price"]) / d["old_price"] * 100)
        elif d.get("avg_price") and d["avg_price"] > d["price"]:
            pct = round((d["avg_price"] - d["price"]) / d["avg_price"] * 100)

        if pct < 10:
            continue # Пропускаем мелкие скидки

        # Топовые скидки (>=25%) выделяем красным кругом 🔴
        if pct >= 25:
            badge = f"🔴 *Скидка -{pct}%!*"
        else:
            badge = f"🔥 *Скидка -{pct}%*"
            
        old_price_val = d["old_price"] or d.get("avg_price", d["price"])
        old_price_str = f" _(старая {round(old_price_val, 2)} грн)_" if old_price_val > d["price"] else ""

        # Ультра-понятный формат: Скидка, Товар (ссылка), Цена, Магазин
        lines.append(
            f"{badge} *[{d['raw_title']}]({d['link']})* — всего за *{d['price']} грн*{old_price_str} (в *{d['store_name']}*)"
        )
    return "\n".join(lines)

def format_basket_report(basket: dict) -> str:
    """Чистый современный формат корзины."""
    if not basket or not basket["stores"]:
        return "🤷‍♂️ *Цены корзины пока не посчитались.*"
        
    sorted_stores = sorted(basket["stores"].items(), key=lambda x: x[1]["total"])
    if not sorted_stores:
        return "🤷‍♂️ *Магазины не найдены.*"
        
    cheapest = sorted_stores[0][1]
    expensive = sorted_stores[-1][1]
    diff = round(expensive["total"] - cheapest["total"])
    
    lines = [
        f"📊 *Где дешевле закупаться сегодня?*\n"
        f"_(Корзина из 25 базовых товаров. Разница: *{diff} грн*)_\n",
        f"🏆 Топ экономии: *{cheapest['name']}* (*{cheapest['total']} грн*)\n"
    ]
    
    for sid, sdata in sorted_stores:
        missing_badge = f" _(нет {len(sdata['missing'])} шт)_" if sdata["missing"] else ""
        lines.append(f" • *{sdata['total']} грн* — {sdata['name']}{missing_badge}")
        
    return "\n".join(lines)
