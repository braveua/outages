"""
main.py - Основной модуль приложения для работы с отключениями
"""

import json
import requests
import time
from pathlib import Path
from datetime import datetime, date
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


def date_ddmmyyyy_to_yyyymmdd(date_str: str) -> str:
    """Конвертирует дату из формата DD.MM.YYYY в YYYY-MM-DD

    Args:
        date_str: Дата в формате DD.MM.YYYY

    Returns:
        str: Дата в формате YYYY-MM-DD или None если формат неверный
    """
    try:
        dt = datetime.strptime(date_str, "%d.%m.%Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def get_today_date_yyyymmdd() -> str:
    """Возвращает сегодняшнюю дату в формате YYYY-MM-DD

    Returns:
        str: Сегодняшняя дата в формате YYYY-MM-DD
    """
    return date.today().strftime("%Y-%m-%d")


def show_schedule_for_date(db: OutageDatabase, device_key: str, date: str):
    """Показывает расписание для устройства на конкретную дату

    Args:
        db: Экземпляр базы данных
        device_key: Ключ устройства
        date: Дата в формате YYYY-MM-DD
    """
    schedule = db.get_device_schedule(device_key, date=date)

    if schedule:
        print(f"\nДанные для устройства '{device_key}' на {date}:")
        for item in schedule[:50]:
            start_time = minutes_to_hhmm(item["start_minute"])
            end_time = minutes_to_hhmm(item["end_minute"])
            print(f"  {item['slot_type']}: {start_time}-{end_time}")
    else:
        print(f"Данные для устройства '{device_key}' на {date} не найдены")


def show_all_devices_for_date(db: OutageDatabase, date: str):
    """Показывает сводку по всем устройствам на конкретную дату

    Args:
        db: Экземпляр базы данных
        date: Дата в формате YYYY-MM-DD
    """
    all_schedules = db.get_all_devices_with_schedules()

    found = False
    for device_key in sorted(all_schedules.keys()):
        schedules = [
            s for s in all_schedules[device_key] if s.get("schedule_date") == date
        ]
        if schedules:
            found = True
            print(f"\nУстройство {device_key}:")
            for item in schedules[:20]:
                start_time = minutes_to_hhmm(item["start_minute"])
                end_time = minutes_to_hhmm(item["end_minute"])
                print(f"  {item['slot_type']}: {start_time}-{end_time}")

    if not found:
        print(f"Данные на дату {date} не найдены")


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
            print(f"\nПример данных для устройства '28.1':")
            schedule = db.get_device_schedule("28.1", is_today=True)
            if schedule:
                for item in schedule[:30]:  # Показываем первые 30 слотов
                    start_time = minutes_to_hhmm(item["start_minute"])
                    end_time = minutes_to_hhmm(item["end_minute"])
                    print(f"  {item['slot_type']}: {start_time}-{end_time}")

            # Инициализируем значения по умолчанию
            default_device = "28.1"
            default_date = get_today_date_yyyymmdd()

            # Интерактивное меню для просмотра по датам
            while True:
                print("\n" + "=" * 50)
                print("Меню:")
                print(
                    f"1 - Просмотр устройства на конкретную дату (по умолчанию: {default_device})"
                )
                print("2 - Просмотр всех устройств на конкретную дату")
                print("3 - Выход")
                print("=" * 50)

                choice = input("Выберите опцию (1-3): ").strip()

                if choice == "1":
                    device_input = input(
                        f"Введите ключ устройства [{default_device}]: "
                    ).strip()
                    device_key = device_input if device_input else default_device

                    today_formatted = f"{default_date.split('-')[2]}.{default_date.split('-')[1]}.{default_date.split('-')[0]}"
                    date_input = input(
                        f"Введите дату DD.MM.YYYY [{today_formatted}]: "
                    ).strip()

                    if date_input:
                        date_yyyymmdd = date_ddmmyyyy_to_yyyymmdd(date_input)
                        if date_yyyymmdd is None:
                            print(
                                "Ошибка: неверный формат даты. Используйте DD.MM.YYYY"
                            )
                            continue
                    else:
                        date_yyyymmdd = default_date

                    show_schedule_for_date(db, device_key, date_yyyymmdd)

                elif choice == "2":
                    today_formatted = f"{default_date.split('-')[2]}.{default_date.split('-')[1]}.{default_date.split('-')[0]}"
                    date_input = input(
                        f"Введите дату DD.MM.YYYY [{today_formatted}]: "
                    ).strip()

                    if date_input:
                        date_yyyymmdd = date_ddmmyyyy_to_yyyymmdd(date_input)
                        if date_yyyymmdd is None:
                            print(
                                "Ошибка: неверный формат даты. Используйте DD.MM.YYYY"
                            )
                            continue
                    else:
                        date_yyyymmdd = default_date

                    show_all_devices_for_date(db, date_yyyymmdd)

                elif choice == "3":
                    print("\nПрограмма завершена!")
                    break

                else:
                    print("Неверная опция. Попробуйте снова.")
        else:
            print("Ошибка при сохранении данных!")
    else:
        print("Не удалось загрузить данные из API")

    print("\nГотово!")


if __name__ == "__main__":
    main()
