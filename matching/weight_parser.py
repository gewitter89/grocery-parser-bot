# -*- coding: utf-8 -*-
"""
matching/weight_parser.py — Парсер веса, объёма и количества из названий товаров.

Обрабатывает форматы:
  "Молоко 2.5% 900мл" → volume_ml=900
  "Гречка 1кг" → weight_g=1000
  "Макарони 400г" → weight_g=400
  "Масло 82.5% 200г" → weight_g=200
  "Яйця 10шт" → piece_count=10
  "Вода 1.5л" → volume_ml=1500
  "Порошок 3,5 кг" → weight_g=3500
  "Туалетний папір 8 рулонів" → piece_count=8
"""
import re
import logging

logger = logging.getLogger(__name__)

# Паттерны для извлечения веса (грамм/килограмм)
WEIGHT_PATTERNS = [
    # 1.5кг, 1,5 кг, 1.5 Кг
    (re.compile(r'(\d+[.,]\d+)\s*(?:кг|kg)\b', re.IGNORECASE), "kg"),
    # 500г, 500 г, 500гр, 500 гр
    (re.compile(r'(\d+)\s*(?:гр?|gr?|грам)\b', re.IGNORECASE), "g"),
    # 1кг, 2кг (без дроби)
    (re.compile(r'(\d+)\s*(?:кг|kg)\b', re.IGNORECASE), "kg"),
]

# Паттерны для объёма (миллилитры/литры)
VOLUME_PATTERNS = [
    # 1.5л, 1,5 л
    (re.compile(r'(\d+[.,]\d+)\s*(?:л|l)\b', re.IGNORECASE), "l"),
    # 900мл, 250 мл
    (re.compile(r'(\d+)\s*(?:мл|ml)\b', re.IGNORECASE), "ml"),
    # 1л, 5л (без дроби)
    (re.compile(r'(\d+)\s*(?:л|l)\b', re.IGNORECASE), "l"),
]

# Паттерны для количества штук
PIECE_PATTERNS = [
    # 10шт, 10 шт, 10 штук
    re.compile(r'(\d+)\s*(?:шт(?:ук)?\.?|pcs)\b', re.IGNORECASE),
    # 8 рулонів, 8рул
    re.compile(r'(\d+)\s*(?:рулон(?:ів|ов)?|рул\.?)\b', re.IGNORECASE),
    # 100 пакетиків, 25пак
    re.compile(r'(\d+)\s*(?:пакетик(?:ів)?|пак\.?)\b', re.IGNORECASE),
    # 10 табл, 20 таблеток
    re.compile(r'(\d+)\s*(?:табл(?:еток)?\.?)\b', re.IGNORECASE),
    # яйця С1 10шт  →  спец. для яиц
    re.compile(r'(\d+)\s*(?:яєць|яиц)\b', re.IGNORECASE),
]


def _parse_number(s: str) -> float:
    """Парсит число, заменяя запятую на точку."""
    return float(s.replace(",", "."))


def parse_weight(text: str) -> float | None:
    """
    Извлечь вес в граммах из текста.
    
    Returns:
        Вес в граммах (float) или None.
        
    Examples:
        >>> parse_weight("Гречка Хуторок 800г")
        800.0
        >>> parse_weight("Рис Басматі 1кг")
        1000.0
        >>> parse_weight("Борошно 1,5 кг")
        1500.0
        >>> parse_weight("Молоко 900мл")  # Это объём, не вес
    """
    if not text:
        return None
    
    for pattern, unit in WEIGHT_PATTERNS:
        match = pattern.search(text)
        if match:
            value = _parse_number(match.group(1))
            if unit == "kg":
                result = value * 1000
            else:  # g
                result = value
            
            # Проверка разумности: вес от 1г до 50кг
            if 1 <= result <= 50000:
                return result
            else:
                logger.warning("Подозрительный вес: %.1f г в '%s'", result, text)
                return None
    
    return None


def parse_volume(text: str) -> float | None:
    """
    Извлечь объём в миллилитрах из текста.
    
    Returns:
        Объём в мл (float) или None.
        
    Examples:
        >>> parse_volume("Молоко 900мл")
        900.0
        >>> parse_volume("Вода 1.5л")
        1500.0
        >>> parse_volume("Олія 1л")
        1000.0
    """
    if not text:
        return None
    
    for pattern, unit in VOLUME_PATTERNS:
        match = pattern.search(text)
        if match:
            value = _parse_number(match.group(1))
            if unit == "l":
                result = value * 1000
            else:  # ml
                result = value
            
            # Проверка разумности: объём от 10 мл до 20 л
            if 10 <= result <= 20000:
                return result
            else:
                logger.warning("Подозрительный объём: %.1f мл в '%s'", result, text)
                return None
    
    return None


def parse_piece_count(text: str) -> int | None:
    """
    Извлечь количество штук из текста.
    
    Returns:
        Количество штук (int) или None.
        
    Examples:
        >>> parse_piece_count("Яйця С1 10шт")
        10
        >>> parse_piece_count("Туалетний папір 8 рулонів")
        8
        >>> parse_piece_count("Чай 100 пакетиків")
        100
    """
    if not text:
        return None
    
    for pattern in PIECE_PATTERNS:
        match = pattern.search(text)
        if match:
            value = int(match.group(1))
            # Проверка разумности: от 1 до 1000 шт
            if 1 <= value <= 1000:
                return value
    
    return None


def calculate_price_per_unit(price: float, weight_g: float = None,
                              volume_ml: float = None,
                              piece_count: int = None,
                              unit_type: str = "kg") -> float | None:
    """
    Рассчитать цену за единицу (кг, литр, штука).
    
    Args:
        price: Цена в грн
        weight_g: Вес в граммах
        volume_ml: Объём в мл
        piece_count: Количество штук
        unit_type: Тип единицы из категории ('kg', 'liter', 'pcs', 'roll')
        
    Returns:
        Цена за кг / литр / штуку (float) или None если невозможно рассчитать.
    """
    if price is None or price <= 0:
        return None
    
    if unit_type in ("kg",) and weight_g and weight_g > 0:
        # Цена за кг
        return round(price / weight_g * 1000, 2)
    
    elif unit_type in ("liter",) and volume_ml and volume_ml > 0:
        # Цена за литр
        return round(price / volume_ml * 1000, 2)
    
    elif unit_type in ("pcs", "roll") and piece_count and piece_count > 0:
        # Цена за штуку
        return round(price / piece_count, 2)
    
    # Fallback: пробуем по весу, потом по объёму
    if weight_g and weight_g > 0:
        return round(price / weight_g * 1000, 2)
    if volume_ml and volume_ml > 0:
        return round(price / volume_ml * 1000, 2)
    if piece_count and piece_count > 0:
        return round(price / piece_count, 2)
    
    return None
