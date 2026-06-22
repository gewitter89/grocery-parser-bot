# -*- coding: utf-8 -*-
"""
Scraper modules for Ukrainian grocery price monitoring.
Each scraper returns standardized product dicts.
"""

from scrapers.zakaz_scraper import ZakazScraper
from scrapers.silpo_scraper import SilpoScraper
from scrapers.fora_scraper import ForaScraper
from scrapers.atb_scraper import ATBScraper

ALL_SCRAPERS = {
    "novus":     lambda: ZakazScraper(store_id="48201031", store_name="Novus"),
    "metro":     lambda: ZakazScraper(store_id="48215610", store_name="Metro"),
    "auchan":    lambda: ZakazScraper(store_id="48246401", store_name="Auchan"),
    "ekomarket": lambda: ZakazScraper(store_id="482800030", store_name="EkoMarket"),
    "silpo":     SilpoScraper,
    "fora":      ForaScraper,
    "atb":       ATBScraper,
}

__all__ = [
    "ZakazScraper",
    "SilpoScraper",
    "ForaScraper",
    "ATBScraper",
    "ALL_SCRAPERS",
]
