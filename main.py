"""
main.py - Основной модуль приложения для работы с отключениями
"""

import json
import requests
import time
from pathlib import Path
from datetime import datetime, date
from database import OutageDatabase
import argparse
import sys


# Кеш для хранения данных и времени последней загрузки
_cache = {"data": None, "timestamp": 0}
CACHE_INTERVAL = 300  # 5 минут в секундах


def load_json_data(url: str, verbose: bool = False) -> dict:
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
        if verbose:
            print(
                f"Используется закешированные данные (возраст: {int(current_time - _cache['timestamp'])}с)"
            )
        return _cache["data"]

    # Загружаем новые данные
    try:
        if verbose:
            print(f"Загрузка данных из {url}...")
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Сохраняем в кеш
        _cache["data"] = data
        _cache["timestamp"] = current_time

        return data
    except requests.exceptions.RequestException as e:
        # Показываем ошибку всегда
        print(f"Ошибка при загрузке данных: {e}")
        # Возвращаем закешированные данные, если они есть
        if _cache["data"] is not None:
            if verbose:
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


def merge_definite_slots(slots: list) -> list:
    """Фильтрует только 'Definite' слоты и объединяет пересекающиеся/смежные интервалы.

    Args:
        slots: Список словарей с ключами start_minute, end_minute, slot_type

    Returns:
        list: Список кортежей (start_minute, end_minute) после слияния
    """
    # Отфильтровать только отключения (Definite)
    defs = [s for s in slots if s.get("slot_type") == "Definite"]
    if not defs:
        return []

    # Сортируем по началу
    defs_sorted = sorted(defs, key=lambda s: s.get("start_minute", 0))

    merged = []
    cur_start = defs_sorted[0]["start_minute"]
    cur_end = defs_sorted[0]["end_minute"]

    for s in defs_sorted[1:]:
        s_start = s.get("start_minute", 0)
        s_end = s.get("end_minute", 0)
        # Если перекрывается или смежно, расширяем текущий интервал
        if s_start <= cur_end:
            cur_end = max(cur_end, s_end)
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = s_start, s_end

    merged.append((cur_start, cur_end))
    return merged


def show_schedule_for_date(db: OutageDatabase, device_key: str, date: str):
    """Показывает расписание для групи на конкретную дату

    Args:
        db: Экземпляр базы данных
        device_key: Ключ групи
        date: Дата в формате YYYY-MM-DD
    """
    schedule = db.get_device_schedule(device_key, date=date)

    if schedule:
        print(f"\nДанные для групи '{device_key}' на {date}:")
        for item in schedule[:50]:
            start_time = minutes_to_hhmm(item["start_minute"])
            end_time = minutes_to_hhmm(item["end_minute"])
            print(f"  {item['slot_type']}: {start_time}-{end_time}")
    else:
        print(f"Данные для групи '{device_key}' на {date} не найдены")


def show_all_devices_for_date(db: OutageDatabase, date: str):
    """Показывает сводку по всем группам на конкретную дату

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
            print(f"\nГрупа {device_key}:")
            for item in schedules[:20]:
                start_time = minutes_to_hhmm(item["start_minute"])
                end_time = minutes_to_hhmm(item["end_minute"])
                print(f"  {item['slot_type']}: {start_time}-{end_time}")

    if not found:
        print(f"Данные на дату {date} не найдены")


def main():
    """Основная функция приложения"""
    # Управляющие сообщения отображаются только при verbose

    parser = argparse.ArgumentParser(description="Просмотр плановых отключений")
    parser.add_argument(
        "--menu", action="store_true", help="Показать интерактивное меню"
    )
    parser.add_argument(
        "--group", default="28.1", help="Ключ групи (по умолчанию 28.1)"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Показывать подробные сообщения (статистика, отладка)",
    )
    parser.add_argument(
        "--date",
        help="Дата в формате DD.MM.YYYY или YYYY-MM-DD (по умолчанию — показывать today/tomorrow)",
    )
    args = parser.parse_args()

    # Инициализация базы данных
    db = OutageDatabase("outages.db")

    # Загрузка данных из API
    api_url = "https://app.yasno.ua/api/blackout-service/public/shutdowns/regions/25/dsos/902/planned-outages"
    data = load_json_data(api_url, verbose=args.verbose)

    if not data:
        print("Не удалось загрузить данные из API")
        print("\nГотово!")
        return

    if args.verbose:
        print("Сохранение данных в базу...")
    success = db.save_json_data(data)

    if not success:
        print("Ошибка при сохранении данных!")
        print("\nГотово!")
        return

    if args.verbose:
        print("Данные успешно сохранены в базу!")

    # Получаем статистику (нужно для времени обновления)
    stats = db.get_stats()
    if args.verbose:
        print(f"\nСтатистика базы данных:")
        print(f"  Групи: {stats['devices_count']}")
        print(f"  Расписаний: {stats['schedules_count']}")
        print(f"  Временных слотов: {stats['slots_count']}")

        for slot_type, count in stats["slots_by_type"].items():
            print(f"    {slot_type}: {count}")

        if stats["last_updated"]:
            print(f"  Последнее обновление: {stats['last_updated']}")

    # Определяем групу и дату вывода
    device = args.group
    if args.date:
        if "." in args.date:
            date_yyyymmdd = date_ddmmyyyy_to_yyyymmdd(args.date)
            if date_yyyymmdd is None:
                print(
                    "Ошибка: неверный формат даты. Используйте DD.MM.YYYY или YYYY-MM-DD"
                )
                return
        else:
            # Проверяем формат YYYY-MM-DD
            try:
                datetime.strptime(args.date, "%Y-%m-%d")
                date_yyyymmdd = args.date
            except ValueError:
                print(
                    "Ошибка: неверный формат даты. Используйте DD.MM.YYYY или YYYY-MM-DD"
                )
                return

        # Показываем данные только для указанной даты; объединим только 'Definite' интервалы
        raw = db.get_device_schedule(device, date=date_yyyymmdd)
        merged = merge_definite_slots(raw)
        if merged:
            print(
                f"\nГрафик плановых отключений для устройства '{device}' на {date_yyyymmdd}:"
            )
            for st, en in merged:
                print(f" 🌑 Отключение: {minutes_to_hhmm(st)} - {minutes_to_hhmm(en)}")
        else:
            print(
                f"Данные для устройства '{device}' на {date_yyyymmdd} не найдены или нет 'Definite' слотов"
            )
        # Покажем время обновления, если есть
        if stats.get("last_updated"):
            try:
                lu = datetime.fromisoformat(stats["last_updated"])
                print(f"  Обновлено в {lu.strftime('%d.%m.%Y %H:%M')}")
            except Exception:
                pass
        return
    else:
        # Формируем компактный отчёт (по умолчанию)
        today_date = get_today_date_yyyymmdd()
        tomorrow_date = (
            date.fromisoformat(today_date) + __import__("datetime").timedelta(days=1)
        ).strftime("%Y-%m-%d")

        # Заголовок
        print("График планових отключений")
        print("----------------------------------")

        # Сегодня
        dt_today = datetime.fromisoformat(today_date)
        print(f"   Сегодня ({dt_today.strftime('%d.%m.%Y')}):")
        schedule_today = db.get_device_schedule(device, date=today_date)
        merged_today = merge_definite_slots(schedule_today)
        if merged_today:
            for st, en in merged_today:
                print(f" 🌑 Отключение: {minutes_to_hhmm(st)} - {minutes_to_hhmm(en)}")
        else:
            print("  Нет данных на сегодня")

        # Завтра
        dt_tom = datetime.fromisoformat(tomorrow_date)
        print(f"   Завтра  ({dt_tom.strftime('%d.%m.%Y')}):")
        schedule_tomorrow = db.get_device_schedule(device, date=tomorrow_date)
        merged_tomorrow = merge_definite_slots(schedule_tomorrow)
        if merged_tomorrow:
            for st, en in merged_tomorrow:
                print(f" 🌑 Отключение: {minutes_to_hhmm(st)} - {minutes_to_hhmm(en)}")
        else:
            print("  Нет данных на завтра")

        # Время обновления в локальном часовом поясе
        if stats.get("last_updated"):
            try:
                lu = datetime.fromisoformat(stats["last_updated"])
                local = lu.astimezone()
                print(f"   Обновлено в {local.strftime('%d.%m.%Y %H:%M')}")
            except Exception:
                pass

    # Если передан флаг --menu, показываем интерактивное меню
    if args.menu:
        default_date = get_today_date_yyyymmdd()
        while True:
            print("\n" + "=" * 50)
            print("Меню:")
            print(f"1 - Просмотр групи на конкретну дату (по умолчанию: {device})")
            print("2 - Просмотр всех групп на конкретну дату")
            print("3 - Выход")
            print("=" * 50)

            choice = input("Выберите опцию (1-3): ").strip()

            if choice == "1":
                device_input = input(f"Введите ключ групи [{device}]: ").strip()
                device_key = device_input if device_input else device

                today_formatted = f"{default_date.split('-')[2]}.{default_date.split('-')[1]}.{default_date.split('-')[0]}"
                date_input = input(
                    f"Введите дату DD.MM.YYYY [{today_formatted}]: "
                ).strip()

                if date_input:
                    date_yyyymmdd = date_ddmmyyyy_to_yyyymmdd(date_input)
                    if date_yyyymmdd is None:
                        print("Ошибка: неверный формат даты. Используйте DD.MM.YYYY")
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
                        print("Ошибка: неверный формат даты. Используйте DD.MM.YYYY")
                        continue
                else:
                    date_yyyymmdd = default_date

                show_all_devices_for_date(db, date_yyyymmdd)

            elif choice == "3":
                print("\nПрограмма завершена!")
                break

            else:
                print("Неверная опция. Попробуйте снова.")

    if args.verbose:
        print("\nГотово!")


if __name__ == "__main__":
    main()
