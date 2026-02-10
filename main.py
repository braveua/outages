"""
main.py - Основной модуль приложения для работы с отключениями
"""

import json
import requests
import time
from pathlib import Path
from datetime import datetime
from database import OutageDatabase


# Кеш для хранения данных и времени последней загрузки
_cache = {"data": None, "timestamp": 0}
CACHE_INTERVAL = 300  # 5 минут в секундах


def load_json_data(url: str) -> dict:
    """Загрузка данных из URL с кешированием

    Args:
        url: URL для загрузки JSON

    Returns:
        dict: Загруженные данные или закешированные данные если кеш еще действителен
    """
    global _cache

    current_time = time.time()

    # Проверяем, не истек ли кеш
    if (
        _cache["data"] is not None
        and (current_time - _cache["timestamp"]) < CACHE_INTERVAL
    ):
        print(
            f"Используется закешированные данные (возраст: {int(current_time - _cache['timestamp'])}с)"
        )
        return _cache["data"]

    # Загружаем новые данные
    try:
        print(f"Загрузка данных из {url}...")
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Сохраняем в кеш
        _cache["data"] = data
        _cache["timestamp"] = current_time

        return data
    except requests.exceptions.RequestException as e:
        print(f"Ошибка при загрузке данных: {e}")
        # Возвращаем закешированные данные, если они есть
        if _cache["data"] is not None:
            print("Используется закешированные данные из-за ошибки подключения")
            return _cache["data"]
        return {}


def minutes_to_hhmm(minutes: int) -> str:
    """Конвертирует минуты от начала дня в формат HH:MM

    Args:
        minutes: Количество минут от начала дня (0-1440)

    Returns:
        str: Время в формате HH:MM
    """
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours:02d}:{mins:02d}"


def main():
    """Основная функция приложения"""
    print("=== Система управления плановыми отключениями ===")

    # Инициализация базы данных
    db = OutageDatabase("outages.db")

    # Загрузка данных из API
    api_url = "https://app.yasno.ua/api/blackout-service/public/shutdowns/regions/25/dsos/902/planned-outages"
    data = load_json_data(api_url)

    if data:
        print("Сохранение данных в базу...")
        success = db.save_json_data(data)

        if success:
            print("Данные успешно сохранены в базу!")

            # Показываем статистику
            stats = db.get_stats()
            print(f"\nСтатистика базы данных:")
            print(f"  Устройств: {stats['devices_count']}")
            print(f"  Расписаний: {stats['schedules_count']}")
            print(f"  Временных слотов: {stats['slots_count']}")

            for slot_type, count in stats["slots_by_type"].items():
                print(f"    {slot_type}: {count}")

            if stats["last_updated"]:
                print(f"  Последнее обновление: {stats['last_updated']}")

            # Пример получения данных для конкретного устройства
            print(f"\nПример данных для устройства '1.1':")
            schedule = db.get_device_schedule("1.1", is_today=True)
            if schedule:
                for item in schedule[:30]:  # Показываем первые 30 слотов
                    start_time = minutes_to_hhmm(item["start_minute"])
                    end_time = minutes_to_hhmm(item["end_minute"])
                    print(f"  {item['slot_type']}: {start_time}-{end_time}")
        else:
            print("Ошибка при сохранении данных!")
    else:
        print("Не удалось загрузить данные из API")

    print("\nГотово!")


if __name__ == "__main__":
    main()
