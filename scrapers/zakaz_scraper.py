# -*- coding: utf-8 -*-
"""
Scraper for Zakaz.ua stores: Novus, Metro, Auchan, EkoMarket.
Uses the public REST API at stores-api.zakaz.ua.
"""

import logging
from typing import Dict, List, Optional

from scrapers.base_scraper import BaseScraper, ProductDict, make_product

logger = logging.getLogger(__name__)

# ── Zakaz.ua Kyiv store IDs ─────────────────────────────────────────────
ZAKAZ_STORES: Dict[str, str] = {
    "48201031":  "Novus",
    "48215610":  "Metro",
    "48246401":  "Auchan",
    "482800030": "EkoMarket",
}

API_BASE = "https://stores-api.zakaz.ua/stores"


class ZakazScraper(BaseScraper):
    """
    Scraper for any Zakaz.ua-powered store.

    Usage::

        scraper = ZakazScraper(store_id="48201031", store_name="Novus")
        products = scraper.scrape({"buckwheat": "гречка"})
    """

    def __init__(self, store_id: str, store_name: Optional[str] = None) -> None:
        name = store_name or ZAKAZ_STORES.get(store_id, f"Zakaz-{store_id}")
        super().__init__(store_id=store_id, store_name=name)
        self._api_url = f"{API_BASE}/{store_id}/products/search/"

    # ── Core search implementation ──────────────────────────────────────
    def search(self, query: str, category_hint: Optional[str] = None) -> List[ProductDict]:
        products: List[ProductDict] = []

        resp = self._get(self._api_url, params={"q": query})

        if resp.status_code != 200:
            logger.warning(
                "[%s] HTTP %d for query '%s'",
                self.store_name, resp.status_code, query,
            )
            return products

        data = resp.json()
        results = data.get("results", [])

        for item in results:
            try:
                product = self._parse_item(item, category_hint)
                if product is not None:
                    products.append(product)
            except Exception as exc:
                logger.debug(
                    "[%s] skip item: %s — %s",
                    self.store_name, exc, item.get("title", "?"),
                )

        return products

    # ── Parse a single Zakaz product JSON object ────────────────────────
    def _parse_item(self, item: dict, category_hint: Optional[str]) -> Optional[ProductDict]:
        title = item.get("title", "").strip()
        if not title:
            return None

        # Price is in kopecks → divide by 100
        raw_price = item.get("price", 0)
        price = raw_price / 100.0 if raw_price else 0.0

        # Discount / old price
        old_price: Optional[float] = None
        discount = item.get("discount")
        if discount and isinstance(discount, dict):
            raw_old = discount.get("old_price", 0)
            if raw_old:
                old_price = raw_old / 100.0

        # Weight in grams (API returns grams already)
        weight_g = item.get("weight")
        if weight_g is not None:
            weight_g = float(weight_g)

        # Volume — Zakaz doesn't always provide it separately; infer None
        volume_ml: Optional[float] = None

        # EAN barcode
        ean = item.get("ean")
        if ean:
            ean = str(ean).strip()

        # Stock
        in_stock = bool(item.get("in_stock", False))

        # Link
        web_url = item.get("web_url", "")
        if not web_url:
            slug = item.get("slug", "")
            web_url = f"https://zakaz.ua/uk/products/{slug}/" if slug else ""

        return make_product(
            store_id=self.store_id,
            raw_title=title,
            price=price,
            old_price=old_price,
            weight_g=weight_g,
            volume_ml=volume_ml,
            ean=ean,
            in_stock=in_stock,
            link=web_url,
            category_hint=category_hint,
        )
