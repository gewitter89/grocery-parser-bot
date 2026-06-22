# -*- coding: utf-8 -*-
"""
telegram_bot/bot.py — Telegram бот на базе direct API запросов.
"""
import requests
import json
import logging
import sqlite3
import time
import os
import sys
from datetime import date

# Разрешаем импорты из родительской папки
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from database.db_manager import DatabaseManager
from analytics.deal_scorer import get_top_deals
from analytics.basket_tracker import calculate_basket_today
from analytics.shopping_optimizer import optimize_shopping_list
from analytics.chart_generator import generate_price_chart
from telegram_bot.formatters import format_deals_report, format_basket_report

logger = logging.getLogger("telegram_bot")

def send_telegram_message(text: str, chat_id: str = None) -> bool:
    """Отправляет текстовое сообщение в Telegram."""
    token = config.TELEGRAM_BOT_TOKEN
    cid = chat_id or config.TELEGRAM_CHAT_ID
    if not token or not cid:
        logger.warning("Telegram token or Chat ID is missing in configuration.")
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": cid,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.error("Failed to send telegram message: %s", e)
        return False

def send_telegram_message_with_button(text: str, button_text: str, button_url: str, chat_id: str = None) -> bool:
    """Отправляет текстовое сообщение с инлайн-кнопкой."""
    token = config.TELEGRAM_BOT_TOKEN
    cid = chat_id or config.TELEGRAM_CHAT_ID
    if not token or not cid:
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    reply_markup = {
        "inline_keyboard": [[
            {"text": button_text, "url": button_url}
        ]]
    }
    payload = {
        "chat_id": cid,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
        "reply_markup": reply_markup
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.error("Failed to send telegram message with button: %s", e)
        return False

def send_telegram_photo(photo_path: str, caption: str = "", chat_id: str = None) -> bool:
    """Отправляет локальное фото в Telegram."""
    token = config.TELEGRAM_BOT_TOKEN
    cid = chat_id or config.TELEGRAM_CHAT_ID
    if not token or not cid:
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    
    # Если передан URL картинки (а не локальный путь)
    if photo_path.startswith("http"):
        payload = {
            "chat_id": cid,
            "photo": photo_path,
            "caption": caption,
            "parse_mode": "Markdown"
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            return resp.status_code == 200
        except Exception as e:
            logger.error("Failed to send remote telegram photo: %s", e)
            return False

    if not os.path.exists(photo_path):
        return False

    try:
        with open(photo_path, 'rb') as photo:
            files = {'photo': photo}
            data = {'chat_id': cid, 'caption': caption, 'parse_mode': 'Markdown'}
            resp = requests.post(url, files=files, data=data, timeout=20)
            return resp.status_code == 200
    except Exception as e:
        logger.error("Failed to send local telegram photo: %s", e)
        return False

def send_telegram_photo_with_button(photo_path: str, caption: str, button_text: str, button_url: str, chat_id: str = None) -> bool:
    """Отправляет фото с кнопкой 'Купить' под ним."""
    token = config.TELEGRAM_BOT_TOKEN
    cid = chat_id or config.TELEGRAM_CHAT_ID
    if not token or not cid:
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    
    reply_markup = {
        "inline_keyboard": [[
            {"text": button_text, "url": button_url}
        ]]
    }
    
    # Если это веб-ссылка
    if photo_path.startswith("http"):
        payload = {
            "chat_id": cid,
            "photo": photo_path,
            "caption": caption,
            "parse_mode": "Markdown",
            "reply_markup": reply_markup
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            return resp.status_code == 200
        except Exception:
            return False

    if not os.path.exists(photo_path):
        return False

    try:
        with open(photo_path, 'rb') as photo:
            files = {'photo': photo}
            data = {
                'chat_id': cid, 
                'caption': caption, 
                'parse_mode': 'Markdown',
                'reply_markup': json.dumps(reply_markup)
            }
            resp = requests.post(url, files=files, data=data, timeout=20)
            return resp.status_code == 200
    except Exception as e:
        logger.error("Failed to send telegram photo with button: %s", e)
        return False

class TelegramBot:
    """Минималистичный поллер для Telegram бота."""
    
    def __init__(self):
        self.db = DatabaseManager(config.DB_PATH)
        self.offset = 0
        
    def poll_updates(self):
        token = config.TELEGRAM_BOT_TOKEN
        if not token:
            logger.error("No Telegram token configured. Poller stopped.")
            return
            
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        logger.info("Starting Telegram Bot poller...")
        
        while True:
            try:
                params = {"offset": self.offset, "timeout": 30}
                resp = requests.get(url, params=params, timeout=35)
                if resp.status_code != 200:
                    time.sleep(5)
                    continue
                    
                data = resp.json()
                for update in data.get("result", []):
                    self.offset = update["update_id"] + 1
                    message = update.get("message")
                    if message:
                        if "text" in message:
                            self.handle_message(message)
                        elif "photo" in message:
                            self.handle_photo(message)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("Error in poller: %s", e)
                time.sleep(5)
                
    def handle_photo(self, msg: dict):
        chat_id = msg["chat"]["id"]
        photo_list = msg.get("photo", [])
        if not photo_list:
            return
            
        # Берем самый большой размер фото
        file_id = photo_list[-1]["file_id"]
        token = config.TELEGRAM_BOT_TOKEN
        
        # Получаем ссылку на файл
        info_url = f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
        try:
            file_info = requests.get(info_url, timeout=10).json()
            file_path = file_info["result"]["file_path"]
            download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
            
            # Сохраняем локально
            local_path = os.path.join(config.BASE_DIR, "temp_receipt.jpg")
            with open(local_path, 'wb') as f:
                f.write(requests.get(download_url, timeout=20).content)
                
            send_telegram_message("📷 Изображение получено. Распознаю чек (это займет около 10-15 сек)...", chat_id)
            
            from matching.ocr_processor import parse_receipt_image
            items = parse_receipt_image(local_path)
            
            if not items:
                send_telegram_message("❌ Не удалось распознать товары или цены на этом фото. Пожалуйста, отправьте более четкое фото чека вертикально.", chat_id)
                return
                
            lines = ["🧾 *Результаты распознавания чека*:\n"]
            total_receipt = 0.0
            
            for it in items:
                lines.append(f"• {it['title']} — {it['price']} грн")
                total_receipt += it["price"]
            
            lines.append(f"\n💰 Итого по чеку: *{round(total_receipt, 2)} грн*")
            send_telegram_message("\n".join(lines), chat_id)
            
            # Удаляем временный файл
            if os.path.exists(local_path):
                os.remove(local_path)
                
        except Exception as e:
            logger.error("Error handling photo: %s", e)
            send_telegram_message("❌ Произошла ошибка при обработке фото.", chat_id)
                
    def handle_message(self, msg: dict):
        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()
        
        if text.startswith("/start"):
            send_telegram_message("Привет! Я автономная система мониторинга цен на продукты в Киеве. Отправьте /deals для просмотра лучших скидок сегодня, /basket для оценки корзины, или пришлите фото чека для его распознавания.", chat_id)
            
        elif text.startswith("/deals"):
            deals = get_top_deals(config.DB_PATH)
            send_telegram_message(format_deals_report(deals), chat_id)
            
        elif text.startswith("/basket"):
            basket = calculate_basket_today(config.DB_PATH)
            send_telegram_message(format_basket_report(basket), chat_id)
            
        elif text.startswith("/status"):
            from monitoring.health_check import generate_system_status
            send_telegram_message(generate_system_status(self.db), chat_id)
            
        elif text.startswith("/find"):
            query = text.replace("/find", "").strip()
            if not query:
                send_telegram_message("Пожалуйста, укажите название товара, например: `/find гречка`", chat_id)
                return
                
            results = self.db.search_products(query)
            if not results:
                send_telegram_message("Ничего не найдено.", chat_id)
                return
                
            lines = ["🔍 *Результаты поиска*:\n"]
            for r in results[:10]:
                lines.append(f"• {r['raw_title']} (ID: {r['id']})")
            send_telegram_message("\n".join(lines), chat_id)
            
        elif text.startswith("/chart"):
            prod_id_str = text.replace("/chart", "").strip()
            if not prod_id_str.isdigit():
                send_telegram_message("Укажите числовой ID товара, например: `/chart 12` (получите ID через /find)", chat_id)
                return
                
            prod_id = int(prod_id_str)
            filepath = generate_price_chart(config.DB_PATH, prod_id)
            if filepath:
                send_telegram_photo(filepath, f"Динамика цен для товара ID {prod_id}", chat_id)
            else:
                send_telegram_message("Не удалось построить график. Проверьте ID или наличие истории цен.", chat_id)
                
        elif text.startswith("/cycle"):
            prod_id_str = text.replace("/cycle", "").strip()
            if not prod_id_str.isdigit():
                send_telegram_message("Укажите числовой ID товара, например: `/cycle 12`", chat_id)
                return
                
            from analytics.trend_analyzer import predict_next_discount
            res = predict_next_discount(config.DB_PATH, int(prod_id_str))
            
            if res["status"] == "insufficient_data":
                send_telegram_message("❌ Недостаточно данных. Нужно не менее 14 дней истории цен на этот товар.", chat_id)
            elif res["status"] == "no_clear_cycles":
                send_telegram_message("❌ У товара нет выраженных циклических колебаний цен (постоянно стабильная или хаотичная цена).", chat_id)
            else:
                send_telegram_message(
                    f"📊 *Прогноз циклов скидок (Товар ID {prod_id_str})*:\n\n"
                    f"🔄 Средний цикл акции: *{res['avg_cycle_days']} дней*\n"
                    f"📅 Последняя акция: {res['last_discount_date']}\n"
                    f"🔮 Прогноз следующей скидки: *{res['next_predicted_date']}*\n"
                    f"⏳ Осталось дней: *{res['days_to_next']}*",
                    chat_id
                )

        elif text.startswith("/optimize"):
            lines_input = text.replace("/optimize", "").strip().split("\n")
            if not lines_input or not lines_input[0]:
                send_telegram_message("Укажите список товаров, каждый на новой строке после команды, например:\n`/optimize`\n`гречка`\n`молоко`", chat_id)
                return
                
            opt = optimize_shopping_list(config.DB_PATH, lines_input)
            response_lines = ["🧠 *Оптимальный план покупок*:\n"]
            
            for sid, sdata in opt["by_store"].items():
                response_lines.append(f"🏪 *{sdata['store_name']}* (Итого: {sdata['subtotal']} грн):")
                for it in sdata["items"]:
                    response_lines.append(f"  • {it['title']} — {it['price']} грн")
                response_lines.append("")
                
            if opt["missing"]:
                response_lines.append(f"❌ Не найдено в магазинах сегодня: {', '.join(opt['missing'])}")
                
            response_lines.append(f"💰 *Общая сумма: {opt['total']} грн*")
            send_telegram_message("\n".join(response_lines), chat_id)
            
        else:
            send_telegram_message("Неизвестная команда. Доступные команды: /deals, /basket, /find, /chart, /cycle, /optimize, /status", chat_id)

if __name__ == "__main__":
    bot = TelegramBot()
    bot.poll_updates()
