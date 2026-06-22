# -*- coding: utf-8 -*-
"""
Scraper for Fora supermarket.
Same EcomCatalogGlobal API as Silpo, different domain.
"""

import logging
from typing import List, Optional

from scrapers.silpo_scraper import SilpoScraper
from scrapers.base_scraper import ProductDict, make_product

logger = logging.getLogger(__name__)

FORA_API_URL = "https://api.catalog.ecom.fora.ua/api/2.0/exec/EcomCatalogGlobal"
FORA_WEB_BASE = "https://fora.ua/product"
DEFAULT_FILIAL = "1"    # Fora Kyiv filial


class ForaScraper(SilpoScraper):
    """
    Scraper for Fora.  Inherits from SilpoScraper because the API
    contract is identical — only the base URL and store_id differ.

    Usage::

        scraper = ForaScraper()
        products = scraper.scrape({"sugar": "цукор"})
    """

    def __init__(self, filial_id: str = DEFAULT_FILIAL) -> None:
        # Call BaseScraper.__init__ directly to avoid Silpo defaults
        super(SilpoScraper, self).__init__(
            store_id="fora", store_name="Fora",
        )
        self.filial_id = filial_id

    # ── Override search to use Fora's URL ───────────────────────────────
    def search(self, query: str, category_hint: Optional[str] = None) -> List[ProductDict]:
        products: List[ProductDict] = []
        page = 1

        while True:
            body = self._build_body(query, page)
            resp = self._post(FORA_API_URL, json_body=body)

            if resp.status_code != 200:
                logger.warning(
                    "[Fora] HTTP %d for query '%s' page %d",
                    resp.status_code, query, page,
                )
                break

            data = resp.json()
            items = data.get("items", [])

            if not items:
                break

            for item in items:
                try:
                    product = self._parse_fora_item(item, category_hint)
                    if product is not None:
                        products.append(product)
                except Exception as exc:
                    logger.debug(
                        "[Fora] skip item: %s — %s",
                        exc, item.get("name", "?"),
                    )

            if len(items) < 100:
                break

            page += 1
            self._polite_delay()

        return products

    # ── Fora-specific parser (store_id + link differ from Silpo) ────────
    def _parse_fora_item(self, item: dict, category_hint: Optional[str]) -> Optional[ProductDict]:
        name = (item.get("name") or "").strip()
        if not name:
            return None

        price = float(item.get("price", 0))
        old_price_raw = item.get("oldPrice")
        old_price = float(old_price_raw) if old_price_raw else None

        weight_g, volume_ml = self._parse_unit(item.get("unit", ""), name)

        slug = item.get("slug", "")
        link = f"{FORA_WEB_BASE}/{slug}" if slug else ""

        return make_product(
            store_id="fora",
            raw_title=name,
            price=price,
            old_price=old_price,
            weight_g=weight_g,
            volume_ml=volume_ml,
            ean=None,
            in_stock=True,
            link=link,
            category_hint=category_hint,
        )
