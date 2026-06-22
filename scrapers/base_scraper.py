# -*- coding: utf-8 -*-
"""
Base scraper with retry logic, UA rotation, rate limiting, and structured logging.
All store-specific scrapers inherit from BaseScraper.
"""

import abc
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# ── User-Agent pool (desktop Chrome / Firefox / Edge, 2024-2025 strings) ────
USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# ── Standardized product dict type ──────────────────────────────────────────
ProductDict = Dict[str, Any]

# Template for a single product record
PRODUCT_TEMPLATE: Dict[str, Any] = {
    "store_id": "",
    "raw_title": "",
    "price": 0.0,
    "old_price": None,
    "weight_g": None,
    "volume_ml": None,
    "ean": None,
    "in_stock": True,
    "link": "",
    "category_hint": None,
}


def make_product(**kwargs: Any) -> ProductDict:
    """Create a standardized product dict, filling defaults for missing keys."""
    product = dict(PRODUCT_TEMPLATE)
    product.update(kwargs)
    return product


class ScrapeRunLog:
    """Simple in-memory log entry for a single scrape run."""

    def __init__(self, store_id: str) -> None:
        self.store_id = store_id
        self.started_at: datetime = datetime.now(timezone.utc)
        self.finished_at: Optional[datetime] = None
        self.status: str = "running"
        self.products_found: int = 0
        self.errors: List[str] = []

    def finish(self, products_found: int, status: str = "ok") -> None:
        self.finished_at = datetime.now(timezone.utc)
        self.products_found = products_found
        self.status = status

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.status = "partial_error"

    def as_dict(self) -> dict:
        return {
            "store_id": self.store_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "status": self.status,
            "products_found": self.products_found,
            "errors": self.errors,
        }


class BaseScraper(abc.ABC):
    """
    Abstract base scraper.

    Features:
      • requests.Session with connection-level retry (3 retries, exponential 1→2→4 s)
      • Random User-Agent per request
      • Polite delay between requests (1.5 – 4.0 s)
      • 15-second connect + read timeout
      • Graceful error handling (log-and-continue)
    """

    # ── Tunables (override in subclasses if needed) ──────────────────────
    MAX_RETRIES: int = 3
    BACKOFF_FACTOR: float = 1.0          # 1s → 2s → 4s
    TIMEOUT: tuple[int, int] = (15, 30)  # (connect, read)
    MIN_DELAY: float = 1.5
    MAX_DELAY: float = 4.0

    def __init__(self, store_id: str, store_name: str) -> None:
        self.store_id = store_id
        self.store_name = store_name
        self._session: Optional[requests.Session] = None
        self.run_log: Optional[ScrapeRunLog] = None

    # ── Session management ───────────────────────────────────────────────
    def _build_session(self):
        from curl_cffi import requests as curl_requests
        session = curl_requests.Session(impersonate="chrome")
        session.headers.update({
            "Accept": "application/json, text/html, */*",
            "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })
        return session

    @property
    def session(self):
        if self._session is None:
            self._session = self._build_session()
        return self._session

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    # ── Helpers ──────────────────────────────────────────────────────────
    def _rotate_ua(self) -> str:
        ua = random.choice(USER_AGENTS)
        self.session.headers["User-Agent"] = ua
        return ua

    def _polite_delay(self) -> None:
        delay = random.uniform(self.MIN_DELAY, self.MAX_DELAY)
        logger.debug("[%s] sleeping %.2f s", self.store_name, delay)
        time.sleep(delay)

    def _adjust_delays(self, success: bool, status_code: Optional[int] = None):
        """Адаптивно изменяет задержки при ошибках или успехе."""
        if not success or status_code in (429, 503):
            # Увеличиваем задержки (максимум до 15 сек)
            self.MIN_DELAY = min(10.0, self.MIN_DELAY * 1.5)
            self.MAX_DELAY = min(15.0, self.MAX_DELAY * 1.5)
            logger.warning("[%s] Request failed or rate limited (%s). Increased delays: %.1f-%.1f s",
                           self.store_name, status_code or "Error", self.MIN_DELAY, self.MAX_DELAY)
        else:
            # Медленно снижаем до исходных базовых настроек
            self.MIN_DELAY = max(1.5, self.MIN_DELAY - 0.1)
            self.MAX_DELAY = max(4.0, self.MAX_DELAY - 0.2)

    def _get(self, url: str, params: Optional[dict] = None):
        """GET with UA rotation and timeout. Raises on transport errors."""
        self._rotate_ua()
        timeout_val = self.TIMEOUT[0] + self.TIMEOUT[1]
        try:
            resp = self.session.get(url, params=params, timeout=timeout_val)
            self._adjust_delays(resp.status_code == 200, resp.status_code)
            return resp
        except Exception as e:
            self._adjust_delays(False)
            raise e

    def _post(self, url: str, json_body: Optional[dict] = None):
        """POST with UA rotation and timeout. Raises on transport errors."""
        self._rotate_ua()
        timeout_val = self.TIMEOUT[0] + self.TIMEOUT[1]
        try:
            resp = self.session.post(url, json=json_body, timeout=timeout_val)
            self._adjust_delays(resp.status_code == 200, resp.status_code)
            return resp
        except Exception as e:
            self._adjust_delays(False)
            raise e

    # ── Main entry point ─────────────────────────────────────────────────
    def scrape(self, keywords: Dict[str, str]) -> List[ProductDict]:
        """
        Run a full scrape for all *keywords*.

        Parameters
        ----------
        keywords : dict
            ``{"category_hint": "search_query", ...}``
            e.g. ``{"buckwheat": "гречка", "milk": "молоко"}``

        Returns
        -------
        list[ProductDict]
        """
        self.run_log = ScrapeRunLog(self.store_id)
        all_products: List[ProductDict] = []

        logger.info(
            "▶ [%s] scrape started  (%d keywords)",
            self.store_name, len(keywords),
        )

        for category_hint, query in keywords.items():
            try:
                products = self.search(query, category_hint=category_hint)
                all_products.extend(products)
                logger.info(
                    "  [%s] '%s' → %d products",
                    self.store_name, query, len(products),
                )
            except Exception as exc:
                error_msg = f"keyword='{query}': {type(exc).__name__}: {exc}"
                logger.error("  [%s] %s", self.store_name, error_msg)
                self.run_log.add_error(error_msg)
            finally:
                self._polite_delay()

        status = "ok" if not self.run_log.errors else "partial_error"
        self.run_log.finish(products_found=len(all_products), status=status)

        logger.info(
            "■ [%s] scrape finished — %d products, status=%s",
            self.store_name, len(all_products), status,
        )
        return all_products

    # ── Abstract method each store must implement ────────────────────────
    @abc.abstractmethod
    def search(self, query: str, category_hint: Optional[str] = None) -> List[ProductDict]:
        """
        Search for *query* and return standardized product dicts.
        Must be implemented by each store scraper.
        """
        ...
