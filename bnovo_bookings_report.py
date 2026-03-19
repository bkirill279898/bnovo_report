#!/usr/bin/env python3
"""
Скрипт для получения бронирований из Bnovo API и расчёта комиссий

Запуск:
    pip install pandas openpyxl python-dateutil requests
    BNOVO_PMS_ID=115233 BNOVO_PASSWORD=<password> python bnovo_commission_report.py

Переменные окружения:
    BNOVO_PMS_ID    - числовой ID объекта в Bnovo PMS
    BNOVO_PASSWORD  - base64-пароль из настроек Bnovo PMS
"""

import os
import sys
import time
import logging
import requests
import pandas as pd
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

load_dotenv()
# ─────────────────────────────────────────────
# КОНФИГУРАЦИЯ
# ─────────────────────────────────────────────
BNOVO_PMS_ID = int(os.getenv("BNOVO_PMS_ID"))
BNOVO_PASSWORD = os.getenv(
    "BNOVO_PASSWORD",
)
BNOVO_BASE_URL = "https://api.pms.bnovo.ru/api/v1"

# ─────────────────────────────────────────────
# ЛОГИРОВАНИЕ
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════
# BNOVO API CLIENT
# ═══════════════════════════════════════════

class BnovoClient:
    """
    Клиент Bnovo PMS REST API v1.
    Авторизация: POST /auth → Bearer-токен.
    Бронирования: GET /bookings с пагинацией limit/offset.
    """

    def __init__(self, pms_id: int, password: str, base_url: str = BNOVO_BASE_URL):
        self.pms_id = pms_id
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
        })
        self._bearer: str | None = None

    def _authenticate(self) -> str:
        """POST /auth → возвращает access_token и сохраняет его в сессии."""
        url = f"{self.base_url}/auth"
        log.info("Авторизация в Bnovo PMS (id=%d)...", self.pms_id)
        r = self.session.post(url, json={"id": self.pms_id, "password": self.password}, timeout=15)
        r.raise_for_status()
        data = r.json()
        if "data" not in data or "access_token" not in data.get("data", {}):
            raise RuntimeError(f"Bnovo /auth: неожиданный ответ: {data}")
        token = data["data"]["access_token"]
        self.session.headers["Authorization"] = f"Bearer {token}"
        self._bearer = token
        log.info("✓ Авторизация успешна")
        return token

    def _get(self, endpoint: str, params: dict | None = None, retries: int = 3) -> dict:
        """GET запрос с автоматическим обновлением токена при 401 и обходом 406."""
        if not self._bearer:
            self._authenticate()
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = self.session.headers.copy()
        headers["Accept"] = "*/*"  # обход HTTP 406
        for attempt in range(1, retries + 1):
            try:
                r = self.session.get(url, params=params, headers=headers, timeout=30)
                if r.status_code == 401:  # токен протух — обновляем
                    log.warning("401 — обновление токена...")
                    self._authenticate()
                    r = self.session.get(url, params=params, headers=headers, timeout=30)
                r.raise_for_status()
                return r.json()
            except requests.HTTPError:
                log.warning("HTTP %s  %s  (попытка %d/%d)", r.status_code, url, attempt, retries)
                if r.status_code in (403,):
                    raise
                time.sleep(2 ** attempt)
            except requests.RequestException as e:
                log.warning("Сеть: %s  (попытка %d/%d)", e, attempt, retries)
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Не удалось получить {url}")

    def get_reservations(self, date_from, date_to, page_size: int = 50) -> list[dict]:
        """
        GET /bookings — пагинация через limit/offset.
        Возвращает список сырых dict-объектов бронирований.
        """
        all_res, offset = [], 0
        while True:
            params = {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "limit": page_size,
                "data_type": "checkedOut",
                "offset": offset,
            }
            data = self._get("/bookings", params=params)
            inner = data.get("data", {})
            batch = inner.get("bookings", [])
            total = inner.get("meta", {}).get("total", 0)

            all_res.extend(batch)
            log.info("  offset=%d  +%d записей  (всего: %d / %d)",
                     offset, len(batch), len(all_res), total)

            if not batch or len(all_res) >= total:
                break
            offset += page_size

        log.info("Итого бронирований загружено: %d", len(all_res))
        return all_res


# ═══════════════════════════════════════════
# РАСЧЁТ КОМИССИЙ
# ═══════════════════════════════════════════

class CommissionCalculator:
    """Расчёт комиссии и даты поступления"""

    CHANNEL_CONFIG = {
        "Яндекс Путешествия (новая версия)": {"commission_rate": 0.25, "days_offset": 14, "special_date_rule": None, "note": "цена неточная, примерная, так как ЯП работают некорректно. комиссия от 15% до 25%"},
        "Roomlink (ранее — Забронируй.ру)": {"commission_rate": 0.23, "days_offset": 0, "special_date_rule": "17th_after_checkout", "note": "✓ уточняется способ оплаты, автоплатеж с 5-15 число"},
        # "ТВИЛ": {"commission_rate": 0.20, "days_offset": 0, "special_date_rule": "checkin_date", "note": "✓ комиссия аванс на сайте, остальное наличными"},
        # "Прямое": {"commission_rate": 0.15, "days_offset": 0, "special_date_rule": "checkin_date", "note": "✓ аванс+оплата"},
        # "Модуль бронирования": {"commission_rate": 0.15, "days_offset": 0, "special_date_rule": "checkin_date", "note": "✓ аванс+оплата"},
        "Otello": {"commission_rate": 0.15, "days_offset": 5, "special_date_rule": None, "note": "✓ автоплатеж через 2-5 дней после брони"},
        # "101hotels.com": {"commission_rate": 0.15, "days_offset": 0, "special_date_rule": "17th_after_checkout", "note": "присылают после взаимазачета с 1-15 число"},
        "Островок!": {"commission_rate": 0.15, "days_offset": 0, "special_date_rule": "25th_after_checkout", "note": "✓ автоплатеж 25 числа"},
        # "Mirturbaz": {"commission_rate": 0.15, "days_offset": 0, "special_date_rule": "25th_after_checkout", "note": "✓ автоплатеж уточняется дата оплаты"},
        "OneTwoTrip!": {"commission_rate": 0.20, "days_offset": 0, "special_date_rule": "15th_after_checkout", "note": "✓ автоплатеж с 5-15 число"},
        # "Суточно.ру": {"commission_rate": 0.20, "days_offset": 0, "special_date_rule": "checkin_date", "note": "✓ аванс на сайте+наличными"},
        # "Avito": {"commission_rate": 0.20, "days_offset": 0, "special_date_rule": "checkin_date", "note": "✓ комиссия аванс на сайте, остальное наличными"}
    }

    @classmethod
    def calculate_commission(cls, channel: str, amount: float) -> float:
        print(channel)
        #commission_rate = cls.CHANNEL_CONFIG.get(channel).get("commission_rate")

        config = cls.CHANNEL_CONFIG.get(channel.strip())
        print("channel:", config)
        print("commission_rate", config["commission_rate"])
        return amount * config["commission_rate"]

    @classmethod
    def calculate_amount_to_receive(cls, channel: str, amount: float, commission: float) -> float:
        return amount - commission

    @classmethod
    def calculate_payment_date(cls, channel: str, checkin_date: datetime, checkout_date: datetime) -> datetime:
        config = cls.CHANNEL_CONFIG.get(channel)
        if not config:
            return checkin_date
        rule = config.get("special_date_rule")
        if rule == "17th_after_checkout":
            # if checkout_date.day <= 1:
            next_month = datetime(checkout_date.year, checkout_date.month, 17) + relativedelta(months=1)
            return datetime(next_month.year, next_month.month, 17)
            #     return datetime(checkout_date.year, checkout_date.month, 15)
            # else:
            #     # Если выезд до 15-го числа - платим 15-го следующего месяца
            #     next_month = checkout_date + relativedelta(months=1)
            #     return datetime(next_month.year, next_month.month, 15)

        if rule == "25th_after_checkout":
            next_month = datetime(checkout_date.year, checkout_date.month, 25) + relativedelta(months=1)
            return datetime(next_month.year, next_month.month, 25)

        elif rule == "checkin_date":
            return checkin_date
        else:
            days_offset = config.get("days_offset", 0)
            return checkout_date + timedelta(days=days_offset)


# ═══════════════════════════════════════════
# ОБРАБОТКА БРОНИРОВАНИЙ
# ═══════════════════════════════════════════

def process_bookings(bookings: list[dict]) -> pd.DataFrame:
    processed_data = []

    for booking in bookings:
        try:
            source = booking.get('source', {})
            channel = source.get('name') if isinstance(source, dict) else booking.get('channel', 'Неизвестно')
            booking_number = booking.get('number') or booking.get('id', '')
            customer = booking.get('customer', {})
            guest_name = f"{customer.get('name','')} {customer.get('surname','')}".strip() if isinstance(customer, dict) else booking.get('guest_name','')
            dates = booking.get('dates', {})
            checkin_date = pd.to_datetime(dates.get('arrival') or booking.get('arrival',''))
            checkout_date = pd.to_datetime(dates.get('departure') or booking.get('departure',''))
            prices = booking.get('prices', [])
            amount = sum(float(p.get('price',0)) for p in prices if isinstance(p, dict)) if prices else float(booking.get('amount',0))
            house_number = booking.get('room_name') or booking.get('house_number','')
            commission = CommissionCalculator.calculate_commission(channel, amount)
            amount_to_receive = CommissionCalculator.calculate_amount_to_receive(channel, amount, commission)
            payment_date = CommissionCalculator.calculate_payment_date(channel, checkin_date, checkout_date)

            record = {
                'Канал': channel,
                '№ брони': str(booking_number),
                'Гость': guest_name,
                'Заезд': checkin_date.strftime('%Y-%m-%d'),
                'Выезд': checkout_date.strftime('%Y-%m-%d'),
                'Номер дома': house_number,
                'Сумма': round(amount,2),
                'Комиссия': round(commission,2),
                'К получению': round(amount_to_receive,2),
                'Дата поступления': payment_date.strftime('%Y-%m-%d'),
                'Примечания': CommissionCalculator.CHANNEL_CONFIG.get(channel).get("note")
            }

            if booking.get('status').get('name') != 'Отменен':
                processed_data.append(record)
        except Exception as e:
            log.warning(f"⚠ Ошибка обработки бронирования: {e}")
            continue

    return pd.DataFrame(processed_data)


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════

def main():
    print("="*60)
    print("СКРИПТ ВЫГРУЗКИ БРОНИРОВАНИЙ ИЗ BNOVO")
    print("="*60)
    print()

    pms_id = BNOVO_PMS_ID
    password = BNOVO_PASSWORD
    if not password:
        password = input("Введите пароль (base64 из настроек Bnovo): ")

    today = datetime.now().date()
    date_from = today - timedelta(days=14)
    date_to = today + relativedelta(months=2)

    print(f"\nПериод выгрузки: {date_from} — {date_to}\n")

    try:
        client = BnovoClient(pms_id, password)
        bookings = client.get_reservations(date_from, date_to)
    except Exception as e:
        log.error(f"✗ Ошибка получения данных: {e}")
        sys.exit(1)

    if not bookings:
        print("\n⚠ Бронирования не найдены")
        return

    print("Обработка бронирований...")
    df = process_bookings(bookings)

    if df.empty:
        print("\n⚠ Нет данных для отображения")
        return

    print(f"✓ Обработано записей: {len(df)}\n")

    print("="*60)
    print("РЕЗУЛЬТАТЫ")
    print("="*60)
    print(df.to_string(index=False))
    print()

    #csv_filename = f"bnovo_bookings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    csv_filename = f"dataprice/bnovo_bookings_latest.csv"
    df.to_csv(csv_filename, index=False, encoding='utf-8-sig')
    print(f"✓ Данные сохранены в файл: {csv_filename}")

    print("\n" + "="*60)
    print("СТАТИСТИКА")
    print("="*60)
    print(f"Всего бронирований: {len(df)}")
    print(f"Общая сумма: {df['Сумма'].sum():,.2f}")
    print(f"Общая комиссия: {df['Комиссия'].sum():,.2f}")
    print(f"Общая сумма к получению: {df['К получению'].sum():,.2f}\n")

    print("Распределение по каналам:")
    channel_summary = df.groupby('Канал').agg({
        'Сумма':'sum',
        'Комиссия':'sum',
        'К получению':'sum'
    })
    print(channel_summary.to_string())
    print()


if __name__ == "__main__":
    main()