# -*- coding: utf-8 -*-
"""
Scraper for ATB supermarket.
HTML scraping of atbmarket.com search results page.
"""

import logging
import re
from typing import List, Optional

from scrapers.base_scraper import BaseScraper, ProductDict, make_product

logger = logging.getLogger(__name__)

try:
    from bs4 import BeautifulSoup
except ImportError:
    raise ImportError(
        "beautifulsoup4 is required for ATBScraper. "
        "Install it with:  pip install beautifulsoup4 lxml"
    )

SEARCH_URL = "https://www.atbmarket.com/sch"
WEB_BASE = "https://www.atbmarket.com"


class ATBScraper(BaseScraper):
    """
    Scraper for ATB (atbmarket.com).

    Usage::

        scraper = ATBScraper()
        products = scraper.scrape({"oil": "олія"})
    """

    def __init__(self) -> None:
        super().__init__(store_id="atb", store_name="ATB")

    # ── Core search ─────────────────────────────────────────────────────
    def search(self, query: str, category_hint: Optional[str] = None) -> List[ProductDict]:
        products: List[ProductDict] = []
        html_text = ""

        try:
            resp = self._get(SEARCH_URL, params={"query": query})
            if resp.status_code == 200:
                html_text = resp.text
            else:
                logger.warning("[ATB] HTTP %d for query '%s'. Trying browser fallback...", resp.status_code, query)
        except Exception as e:
            logger.warning("[ATB] Error during HTTP get: %s. Trying browser fallback...", e)

        # Fallback на Playwright если прямой запрос не сработал
        if not html_text:
            try:
                from scrapers.browser_scraper import BrowserScraper
                bs = BrowserScraper(headless=True)
                full_url = f"{SEARCH_URL}?query={query}"
                html_text = bs.scrape_url_html(full_url)
                bs.close()
            except Exception as e:
                logger.error("[ATB] Browser fallback failed: %s", e)

        if not html_text:
            return products

        soup = BeautifulSoup(html_text, "html.parser")
        cards = soup.select(".catalog-item.js-product-container")

        if not cards:
            logger.info("[ATB] no products found for '%s'", query)
            return products

        for card in cards:
            try:
                product = self._parse_card(card, category_hint)
                if product is not None:
                    products.append(product)
            except Exception as exc:
                logger.debug("[ATB] skip card: %s", exc)

        return products

    # ── Parse a single product card ─────────────────────────────────────
    def _parse_card(self, card, category_hint: Optional[str]) -> Optional[ProductDict]:
        # Title  — <a class="catalog-item__title">
        title_el = card.select_one(".catalog-item__title")
        if title_el is None:
            return None
        title = title_el.get_text(strip=True)
        if not title:
            return None

        # Link (relative → absolute)
        href = title_el.get("href", "")
        link = f"{WEB_BASE}{href}" if href and not href.startswith("http") else href

        # Price — <div class="product-price">
        price = self._extract_price(card, ".product-price__top")
        if price is None:
            price = self._extract_price(card, ".product-price")
        if price is None:
            price = 0.0

        # Old price (crossed-out)
        old_price = self._extract_price(card, ".product-price__bottom")

        # Weight / volume from title
        weight_g, volume_ml = self._parse_weight_volume(title)

        # Stock — ATB search page only shows available items
        in_stock = True

        return make_product(
            store_id="atb",
            raw_title=title,
            price=price,
            old_price=old_price,
            weight_g=weight_g,
            volume_ml=volume_ml,
            ean=None,
            in_stock=in_stock,
            link=link,
            category_hint=category_hint,
        )

    # ── Price extraction helper ─────────────────────────────────────────
    @staticmethod
    def _extract_price(card, selector: str) -> Optional[float]:
        """Extract a numeric price from a card element matching *selector*."""
        el = card.select_one(selector)
        if el is None:
            return None

        text = el.get_text(strip=True)
        # Remove non-numeric chars except dot/comma: "45,90 ₴" → "45.90"
        cleaned = re.sub(r"[^\d.,]", "", text).replace(",", ".")
        # Handle "4590" (kopeck-style without separator) — unlikely but safe
        if not cleaned:
            return None

        try:
            return float(cleaned)
        except ValueError:
            return None

    # ── Weight / volume from product title ──────────────────────────────
    @staticmethod
    def _parse_weight_volume(title: str) -> tuple[Optional[float], Optional[float]]:
        weight_g: Optional[float] = None
        volume_ml: Optional[float] = None

        t = title.lower()

        kg_m = re.search(r"(\d+[.,]?\d*)\s*кг", t)
        g_m = re.search(r"(\d+[.,]?\d*)\s*г(?!р)", t)
        if kg_m:
            weight_g = float(kg_m.group(1).replace(",", ".")) * 1000
        elif g_m:
            weight_g = float(g_m.group(1).replace(",", "."))

        ml_m = re.search(r"(\d+[.,]?\d*)\s*мл", t)
        l_m = re.search(r"(\d+[.,]?\d*)\s*л(?!і)", t)
        if ml_m:
            volume_ml = float(ml_m.group(1).replace(",", "."))
        elif l_m:
            volume_ml = float(l_m.group(1).replace(",", ".")) * 1000

        return weight_g, volume_ml
