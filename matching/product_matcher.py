# -*- coding: utf-8 -*-
"""
matching/product_matcher.py — Нормализация и сопоставление товаров.

Задача: определить, что "Молоко Яготинське пастеризоване 2.6% 900мл"
и "Яготинське молоко 2,6% 900 мл" — это ОДИН И ТОТ ЖЕ товар.

Стратегия матчинга:
  1. EAN (штрихкод) — точное совпадение (100% уверенность)
  2. normalized_title + weight/volume — если нет EAN
  3. Jaccard similarity — fallback для fuzzy matching
"""
import re
import unicodedata
import logging

logger = logging.getLogger(__name__)

# Слова, которые убираем при нормализации (не несут смысла для сравнения)
STOP_WORDS = {
    # Украинские
    "тм", "тов", "пп", "пакет", "упаковка", "уп", "шт",
    "ваговий", "вагова", "ваговий", "фасований", "фасована",
    "в", "із", "з", "для", "на", "та", "і", "або", "по",
    # Русские
    "тм", "тов", "упаковка", "шт", "штук",
    "весовой", "весовая", "фасованный", "фасованная",
    "в", "из", "для", "на", "и", "или", "по", "с",
    # Английские
    "tm", "pcs", "pack",
}

# Распространённые бренды (для извлечения)
BRAND_PATTERNS = [
    "яготинське", "яготинськ", "яготин",
    "ферма", "президент", "простоквашино",
    "галичина", "молокія", "злагода",
    "хуторок", "своя лінія", "повна чаша",
    "бабусин продукт", "добра ферма",
    "мівіна", "макфа", "чумак", "торчин",
    "олейна", "щедрий дар", "королівський смак",
    "дарина", "цаоглашеница",
    "fairy", "gala", "persil", "ariel", "tide",
    "colgate", "blend-a-med", "oral-b",
    "rexona", "dove", "nivea", "head&shoulders",
    "zewa", "рута", "сніжна панда", "диво",
]


def normalize_title(raw_title: str) -> str:
    """
    Нормализовать название товара для сравнения.
    
    Шаги:
      1. Привести к нижнему регистру
      2. Убрать спецсимволы (кроме букв, цифр, пробелов, точек, запятых)
      3. Убрать стоп-слова
      4. Убрать вес/объём/количество (они хранятся отдельно)
      5. Сжать пробелы
      6. Отсортировать слова (чтобы "молоко яготинське" == "яготинське молоко")
      
    Examples:
        >>> normalize_title("Молоко Яготинське пастеризоване 2.6% 900мл")
        "2.6% молоко пастеризоване яготинське"
        >>> normalize_title("Гречка ТМ Хуторок 800г")
        "гречка хуторок"
    """
    if not raw_title:
        return ""
    
    text = raw_title.lower().strip()
    
    # Удалить содержимое в скобках (часто мусор: "(Акція)", "(новинка)")
    text = re.sub(r'\([^)]*\)', '', text)
    text = re.sub(r'\[[^\]]*\]', '', text)
    
    # Убрать символы ™, ®, ©
    text = text.replace("™", "").replace("®", "").replace("©", "")
    
    # Убрать вес/объём/количество из текста (они хранятся отдельно)
    text = re.sub(r'\d+[.,]?\d*\s*(?:кг|kg|гр?|gr?|грам|мл|ml|л|l)\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\d+\s*(?:шт(?:ук)?\.?|pcs|рулон(?:ів|ов)?|рул\.?|пакетик(?:ів)?|пак\.?)\b', '', text, flags=re.IGNORECASE)
    
    # Оставить только буквы, цифры, точки, запятые, проценты, пробелы
    text = re.sub(r'[^a-zа-яёіїєґ0-9.,% ]', ' ', text)
    
    # Убрать стоп-слова
    words = text.split()
    words = [w for w in words if w not in STOP_WORDS and len(w) > 0]
    
    # Отсортировать слова для order-independent сравнения
    words.sort()
    
    return " ".join(words)


def extract_brand(raw_title: str) -> str | None:
    """
    Попробовать извлечь бренд из названия товара.
    
    Returns:
        Название бренда или None.
    """
    if not raw_title:
        return None
    
    title_lower = raw_title.lower()
    for brand in BRAND_PATTERNS:
        if brand in title_lower:
            return brand.title()
    
    return None


def jaccard_similarity(s1: str, s2: str) -> float:
    """
    Рассчитать Jaccard Similarity между двумя строками (по словам).
    
    Returns:
        Значение от 0.0 до 1.0
    """
    if not s1 or not s2:
        return 0.0
    
    set1 = set(s1.lower().split())
    set2 = set(s2.lower().split())
    
    intersection = set1 & set2
    union = set1 | set2
    
    if not union:
        return 0.0
    
    return len(intersection) / len(union)


def cosine_similarity_tfidf(s1: str, s2: str) -> float:
    """Вычисляет косинусное сходство между двумя названиями используя TF-IDF."""
    if not s1 or not s2:
        return 0.0
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        tfidf = vectorizer.fit_transform([s1, s2])
        return float(cosine_similarity(tfidf[0:1], tfidf[1:2])[0][0])
    except Exception:
        return 0.0


def products_match(p1: dict, p2: dict, threshold: float = 0.7) -> bool:
    """
    Определить, являются ли два продукта одним и тем же товаром.
    """
    # 1. EAN match
    if p1.get("ean") and p2.get("ean") and p1["ean"] == p2["ean"]:
        return True
    
    # 2. Exact normalized title + weight
    if (p1.get("normalized_title") and p2.get("normalized_title")
            and p1["normalized_title"] == p2["normalized_title"]):
        w1 = p1.get("weight_g")
        w2 = p2.get("weight_g")
        if w1 == w2:
            return True
        if w1 and w2:
            return False
        return True
    
    # 3. Jaccard similarity
    sim = jaccard_similarity(
        p1.get("normalized_title", ""),
        p2.get("normalized_title", "")
    )
    # Если Jaccard ниже порога, пробуем TF-IDF Косинусное сходство (векторное)
    if sim < threshold:
        sim = cosine_similarity_tfidf(
            p1.get("normalized_title", ""),
            p2.get("normalized_title", "")
        )

    if sim >= threshold:
        w1, w2 = p1.get("weight_g"), p2.get("weight_g")
        if w1 and w2 and abs(w1 - w2) > 10:
            return False
        v1, v2 = p1.get("volume_ml"), p2.get("volume_ml")
        if v1 and v2 and abs(v1 - v2) > 10:
            return False
        return True
    
    return False


def extract_fat_percentage(raw_title: str) -> float | None:
    """
    Извлечь жирность из названия (для молочки).
    
    Examples:
        >>> extract_fat_percentage("Молоко 2.6%")
        2.6
        >>> extract_fat_percentage("Масло 82,5%")
        82.5
    """
    if not raw_title:
        return None
    
    match = re.search(r'(\d+[.,]?\d*)\s*%', raw_title)
    if match:
        value = float(match.group(1).replace(",", "."))
        if 0 < value <= 100:
            return value
    
    return None
