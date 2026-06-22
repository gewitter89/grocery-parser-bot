# -*- coding: utf-8 -*-
"""
analytics/chart_generator.py — Генератор графиков изменения цен.
"""
import os
import sqlite3
from datetime import datetime
import config

# Безопасный импорт matplotlib без GUI
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def generate_price_chart(db_path: str, product_id: int, days: int = 90) -> str | None:
    """
    Генерирует PNG график изменения цен для товара и сохраняет его в папку charts.
    Возвращает путь к сгенерированному файлу.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        product = conn.execute("SELECT raw_title FROM products WHERE id = ?", (product_id,)).fetchone()
        if not product:
            return None
            
        title = product["raw_title"]
        
        # Получаем цены
        rows = conn.execute("""
            SELECT date, price, s.name as store_name
            FROM price_history ph
            JOIN stores s ON ph.store_id = s.id
            WHERE product_id = ? AND date >= date('now', ?)
            ORDER BY date ASC
        """, (product_id, f"-{days} days")).fetchall()
        
        if not rows:
            return None
            
        # Группируем по магазинам
        data_by_store = {}
        for r in rows:
            store = r["store_name"]
            if store not in data_by_store:
                data_by_store[store] = {"dates": [], "prices": []}
            
            # Парсим дату для красивого вывода
            d_obj = datetime.strptime(r["date"], "%Y-%m-%d")
            data_by_store[store]["dates"].append(d_obj)
            data_by_store[store]["prices"].append(r["price"])
            
        plt.figure(figsize=(10, 5))
        for store, sdata in data_by_store.items():
            plt.plot(sdata["dates"], sdata["prices"], marker='o', linestyle='-', label=store)
            
        plt.title(f"Динамика цен: {title}")
        plt.xlabel("Дата")
        plt.ylabel("Цена (грн)")
        plt.grid(True, linestyle='--', alpha=0.6)
        plt.legend()
        plt.gcf().autofmt_xdate() # Форматирование дат по оси X
        
        # Сохранение файла
        os.makedirs(config.CHART_DIR, exist_ok=True)
        filepath = os.path.join(config.CHART_DIR, f"chart_prod_{product_id}.png")
        plt.savefig(filepath, dpi=150, bbox_inches='tight')
        plt.close()
        
        return filepath
    except Exception as e:
        # Не ломаем процесс если график не построился
        print(f"Chart generation failed: {e}")
        return None
    finally:
        conn.close()


def generate_deals_collage(deals: list) -> str | None:
    """
    Скачивает картинки для ТОП-6 товаров и собирает их в красивую плитку 2х3 с ценами.
    Возвращает путь к сгенерированному PNG-файлу.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        import requests
        from io import BytesIO
    except ImportError:
        logger.error("PIL (Pillow) is required for collage generation.")
        return None

    # Настройки сетки
    card_w, card_h = 300, 350
    cols, rows = 2, 3
    collage_w = card_w * cols
    collage_h = card_h * rows

    collage = Image.new("RGB", (collage_w, collage_h), "#F3F4F6")
    draw = ImageDraw.Draw(collage)
    
    # Дефолтная заглушка если картинка товара не скачается
    default_img = Image.new("RGB", (280, 200), "#E5E7EB")

    for idx, d in enumerate(deals[:6]):
        c = idx % cols
        r = idx // cols
        
        # Вычисляем координаты левого верхнего угла карточки товара
        x_off = c * card_w
        y_off = r * card_h
        
        # Рисуем белую подложку под карточку товара
        draw.rectangle([x_off + 10, y_off + 10, x_off + card_w - 10, y_off + card_h - 10], fill="#FFFFFF", outline="#E5E7EB", width=1)
        
        # Попробуем скачать картинку товара
        img = None
        # Заглушка для фото
        photo_url = "https://images.unsplash.com/photo-1542838132-92c53300491e?w=300"
        
        try:
            # Для реального сбора картинок можно использовать сохраненный URL
            resp = requests.get(photo_url, timeout=5)
            if resp.status_code == 200:
                raw_img = Image.open(BytesIO(resp.content))
                img = raw_img.resize((280, 200))
        except Exception:
            pass

        if not img:
            img = default_img

        # Вставляем фото товара
        collage.paste(img, (x_off + 10, y_off + 15))
        
        # Рисуем ценник и название
        title_short = d["raw_title"][:22] + "..." if len(d["raw_title"]) > 22 else d["raw_title"]
        draw.text((x_off + 20, y_off + 225), f"{idx+1}. {title_short}", fill="#1F2937")
        
        pct_str = ""
        if d["old_price"] and d["old_price"] > d["price"]:
            pct = round((d["old_price"] - d["price"]) / d["old_price"] * 100)
            pct_str = f" (-{pct}%)"
            
        draw.text((x_off + 20, y_off + 255), f"🔥 {d['price']} грн{pct_str}", fill="#EF4444")
        draw.text((x_off + 20, y_off + 285), f"🏪 {d['store_name']}", fill="#4B5563")

    collage_path = os.path.join(config.CHART_DIR, "deals_collage.png")
    collage.save(collage_path)
    return collage_path
