# -*- coding: utf-8 -*-
"""
scrapers/browser_scraper.py — Скрапер на базе Playwright (Stealth).

Позволяет рендерить JavaScript, прокручивать страницы, эмулировать поведение человека
и обходить Cloudflare Turnstile/капчи абсолютно бесплатно.
"""
import logging
import random
import time
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Попытаемся импортировать playwright
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.error("Playwright is not installed. BrowserScraper will not function.")


class BrowserScraper:
    """Управляющий класс для автоматизации браузера с защитой от детекции ботов."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None

    def start(self):
        """Запустить браузер с настройками маскировки."""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright is not installed.")

        self.pw = sync_playwright().start()
        
        # Запуск Chromium с флагами маскировки
        self.browser = self.pw.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1920,1080",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ]
        )
        
        # Создаем контекст с эмуляцией реального экрана и локали
        self.context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="uk-UA",
            timezone_id="Europe/Kyiv",
        )
        
        # Инъекция скрипта для сокрытия WebDriver (Stealth эффект)
        self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            window.chrome = {
                runtime: {}
            };
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
        """)
        
        self.page = self.context.new_page()
        logger.info("Playwright Browser started successfully.")

    def close(self):
        """Закрыть браузер."""
        if self.page:
            self.page.close()
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.pw:
            self.pw.stop()
        logger.info("Playwright Browser closed.")

    def human_scroll(self):
        """Плавная прокрутка страницы вниз для триггера загрузки динамического контента."""
        if not self.page:
            return
        try:
            total_height = self.page.evaluate("document.body.scrollHeight")
            current_scroll = 0
            while current_scroll < total_height:
                step = random.randint(200, 450)
                current_scroll += step
                self.page.evaluate(f"window.scrollTo(0, {current_scroll})")
                time.sleep(random.uniform(0.2, 0.5))
                total_height = self.page.evaluate("document.body.scrollHeight")
        except Exception as e:
            logger.warning("Error during human scroll: %s", e)

    def scrape_url_html(self, url: str) -> str:
        """Открывает URL, ждет загрузки и возвращает HTML-содержимое страницы."""
        if not self.page:
            self.start()
            
        try:
            logger.info("Navigating to URL: %s", url)
            self.page.goto(url, wait_until="networkidle", timeout=45000)
            
            # Рандомная задержка
            time.sleep(random.uniform(1.5, 3.0))
            
            # Прокручиваем страницу
            self.human_scroll()
            time.sleep(1.0)
            
            # Возвращаем HTML
            return self.page.content()
        except Exception as e:
            logger.error("Failed to scrape URL %s with browser: %s", url, e)
            return ""
            
    def solve_simple_checkbox(self) -> bool:
        """
        Пробует кликнуть на чекбокс 'Я не робот' / Turnstile,
        если он присутствует на экране.
        """
        if not self.page:
            return False
        try:
            # Ищем фреймы Cloudflare или reCAPTCHA
            frames = self.page.frames
            for frame in frames:
                if "challenge" in frame.url or "turnstile" in frame.url:
                    # Ищем кнопку/чекбокс внутри фрейма
                    checkbox = frame.locator("input[type='checkbox'], #challenge-stage")
                    if checkbox.is_visible():
                        logger.info("Found Cloudflare Turnstile checkbox. Clicking...")
                        checkbox.click()
                        time.sleep(3.0)
                        return True
            return False
        except Exception as e:
            logger.debug("Failed to click turnstile checkbox: %s", e)
            return False
