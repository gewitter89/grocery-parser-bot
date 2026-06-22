# -*- coding: utf-8 -*-
"""
Scraper for Silpo supermarket.
Uses the EcomCatalogGlobal POST API at api.catalog.ecom.silpo.ua.
"""

import logging
import re
from typing import List, Optional

from scrapers.base_scraper import BaseScraper, ProductDict, make_product

logger = logging.getLogger(__name__)

API_URL = "https://api.catalog.ecom.silpo.ua/api/2.0/exec/EcomCatalogGlobal"
DEFAULT_FILIAL = "2405"        # Kyiv main filial
PRODUCTS_PER_PAGE = 100
WEB_BASE = "https://silpo.ua/product"


class SilpoScraper(BaseScraper):
    """
    Scraper for Silpo.

    Usage::

        scraper = SilpoScraper()
        products = scraper.scrape({"milk": "молоко"})
    """

    def __init__(self, filial_id: str = DEFAULT_FILIAL) -> None:
        super().__init__(store_id="silpo", store_name="Silpo")
        self.filial_id = filial_id

    # ── Core search ─────────────────────────────────────────────────────
    def search(self, query: str, category_hint: Optional[str] = None) -> List[ProductDict]:
        products: List[ProductDict] = []
        page = 1

        while True:
            body = self._build_body(query, page)
            resp = self._post(API_URL, json_body=body)

            if resp.status_code != 200:
                logger.warning(
                    "[Silpo] HTTP %d for query '%s' page %d",
                    resp.status_code, query, page,
                )
                break

            data = resp.json()
            items = data.get("items", [])

            if not items:
                break

            for item in items:
                try:
                    product = self._parse_item(item, category_hint)
                    if product is not None:
                        products.append(product)
                except Exception as exc:
                    logger.debug(
                        "[Silpo] skip item: %s — %s",
                        exc, item.get("name", "?"),
                    )

            # Stop if we got fewer items than a full page (last page)
            if len(items) < PRODUCTS_PER_PAGE:
                break

            page += 1
            self._polite_delay()

        return products

    # ── Build request body ──────────────────────────────────────────────
    def _build_body(self, query: str, page: int) -> dict:
        return {
            "method": "GetSimpleCatalogItems",
            "data": {
                "customFilter": query,
                "filialId": self.filial_id,
                "skuPerPage": PRODUCTS_PER_PAGE,
                "pageNumber": page,
            },
        }

    # ── Parse single item ───────────────────────────────────────────────
    def _parse_item(self, item: dict, category_hint: Optional[str]) -> Optional[ProductDict]:
        name = (item.get("name") or "").strip()
        if not name:
            return None

        price = float(item.get("price", 0))
        old_price_raw = item.get("oldPrice")
        old_price = float(old_price_raw) if old_price_raw else None

        # Parse weight / volume from the 'unit' field (e.g. "1000 г", "900 мл")
        weight_g, volume_ml = self._parse_unit(item.get("unit", ""), name)

        slug = item.get("slug", "")
        link = f"{WEB_BASE}/{slug}" if slug else ""

        return make_product(
            store_id="silpo",
            raw_title=name,
            price=price,
            old_price=old_price,
            weight_g=weight_g,
            volume_ml=volume_ml,
            ean=None,
            in_stock=True,        # Silpo API only returns in-stock items
            link=link,
            category_hint=category_hint,
        )

    # ── Extract weight / volume from unit string or title ───────────────
    @staticmethod
    def _parse_unit(unit: str, title: str) -> tuple[Optional[float], Optional[float]]:
        weight_g: Optional[float] = None
        volume_ml: Optional[float] = None

        text = f"{unit} {title}".lower()

        # Weight: "1000 г", "1.5 кг"
        kg_match = re.search(r"(\d+[.,]?\d*)\s*кг", text)
        g_match = re.search(r"(\d+[.,]?\d*)\s*г(?!р)", text)  # г but not гр (грн)

        if kg_match:
            weight_g = float(kg_match.group(1).replace(",", ".")) * 1000
        elif g_match:
            weight_g = float(g_match.group(1).replace(",", "."))

        # Volume: "900 мл", "1 л"
        l_match = re.search(r"(\d+[.,]?\d*)\s*л(?!і)", text)   # л but not лі
        ml_match = re.search(r"(\d+[.,]?\d*)\s*мл", text)

        if ml_match:
            volume_ml = float(ml_match.group(1).replace(",", "."))
        elif l_match:
            volume_ml = float(l_match.group(1).replace(",", ".")) * 1000

        return weight_g, volume_ml
