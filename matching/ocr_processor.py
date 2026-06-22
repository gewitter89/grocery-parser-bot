# -*- coding: utf-8 -*-
"""
matching/ocr_processor.py — Обработчик изображений чеков (OCR).

Распознает строки товаров и цены с помощью easyocr.
"""
import logging
import re
import os

logger = logging.getLogger(__name__)

# Инициализируем ридер лениво (чтобы не жрать память при старте)
_reader = None

def get_ocr_reader():
    global _reader
    if _reader is None:
        try:
            import easyocr
            # Загружаем модель для украинского и английского
            logger.info("Initializing EasyOCR (uk, en)...")
            _reader = easyocr.Reader(['uk', 'en'], gpu=False)
        except Exception as e:
            logger.error("Failed to load EasyOCR: %s", e)
    return _reader

def parse_receipt_image(image_path: str) -> list:
    """
    Распознает чек на фото.
    Возвращает список словарей: [{"title": "Гречка", "price": 49.90}, ...]
    """
    reader = get_ocr_reader()
    if not reader:
        return []

    try:
        # Считываем текст
        results = reader.readtext(image_path, detail=0)
        logger.info("OCR parsed %d lines from receipt", len(results))

        parsed_items = []
        
        # Простой эвристический парсер строк чека:
        # Ищем названия товаров и цены. Обычно цена идет либо в той же строке, либо на следующей.
        # Шаблон цены: цифры с копейками через точку или запятую, например "49.90", "120,00"
        price_pattern = re.compile(r'(\d+)[.,](\d{2})\b')

        for i, text in enumerate(results):
            # Проверяем, содержит ли текущая строка цену
            match = price_pattern.search(text)
            if match:
                price = float(f"{match.group(1)}.{match.group(2)}")
                # Название товара обычно слева от цены или в предыдущей строке
                title_part = price_pattern.sub('', text).strip()
                
                # Если название короткое, попробуем взять предыдущую строку
                if len(title_part) < 3 and i > 0:
                    title_part = results[i-1].strip()

                # Убираем лишние спецсимволы из названия
                title_part = re.sub(r'[^a-zA-Zа-яА-Яёіїєґ0-9\s]', '', title_part).strip()

                if len(title_part) > 2 and price > 0:
                    parsed_items.append({
                        "title": title_part,
                        "price": price
                    })
        
        return parsed_items
    except Exception as e:
        logger.error("OCR processing error: %s", e)
        return []
