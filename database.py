# Создайте файл database.py
"""
database.py - Модуль для работы с базой данных отключений
"""

import sqlite3
import json
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path


class OutageDatabase:
    def __init__(self, db_path: str = "outages.db"):
        """Инициализация базы данных"""
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        """Инициализация структуры базы данных"""
        with sqlite3.connect(self.db_path) as conn:
            # Создаем таблицы
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_key TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS outage_schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER NOT NULL,
                    schedule_date DATE NOT NULL,
                    status TEXT NOT NULL,
                    timezone_offset TEXT,
                    is_today BOOLEAN NOT NULL,
                    updated_on TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
                    UNIQUE(device_id, schedule_date, is_today)
                );
                
                CREATE TABLE IF NOT EXISTS time_slots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id INTEGER NOT NULL,
                    start_minute INTEGER NOT NULL,
                    end_minute INTEGER NOT NULL,
                    slot_type TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (schedule_id) REFERENCES outage_schedules(id) ON DELETE CASCADE,
                    CHECK (start_minute >= 0 AND start_minute < 1440),
                    CHECK (end_minute > start_minute AND end_minute <= 1440),
                    CHECK (slot_type IN ('Definite', 'NotPlanned'))
                );
                
                CREATE INDEX IF NOT EXISTS idx_devices_key ON devices(device_key);
                CREATE INDEX IF NOT EXISTS idx_schedules_device_date ON outage_schedules(device_id, schedule_date);
                CREATE INDEX IF NOT EXISTS idx_schedules_status ON outage_schedules(status);
                CREATE INDEX IF NOT EXISTS idx_slots_schedule ON time_slots(schedule_id);
                CREATE INDEX IF NOT EXISTS idx_slots_type ON time_slots(slot_type);
            """)

    def save_json_data(self, json_data: Dict[str, Any]) -> bool:
        """
        Сохранение данных из JSON в базу данных

        Args:
            json_data: Данные в формате JSON из файла planned-outages.json

        Returns:
            bool: True если успешно, False в случае ошибки
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row

                for device_key, device_data in json_data.items():
                    # 1. Сохраняем или получаем устройство
                    device_id = self._get_or_create_device(conn, device_key)

                    # 2. Обрабатываем данные для "today"
                    today_data = device_data.get("today")
                    if today_data:
                        self._save_schedule(
                            conn,
                            device_id,
                            today_data,
                            is_today=True,
                            updated_on=device_data.get("updatedOn"),
                        )

                    # 3. Обрабатываем данные для "tomorrow"
                    tomorrow_data = device_data.get("tomorrow")
                    if tomorrow_data:
                        self._save_schedule(
                            conn,
                            device_id,
                            tomorrow_data,
                            is_today=False,
                            updated_on=device_data.get("updatedOn"),
                        )

                # Нормализуем расписания после импорта всех устройств (удаляем дубликаты по device_id+schedule_date)
                self._normalize_schedules(conn)

                return True
        except Exception as e:
            print(f"Ошибка при сохранении данных: {e}")
            return False

    def _get_or_create_device(self, conn: sqlite3.Connection, device_key: str) -> int:
        """Получаем или создаем запись устройства"""
        cursor = conn.execute(
            "SELECT id FROM devices WHERE device_key = ?", (device_key,)
        )
        result = cursor.fetchone()

        if result:
            return result[0]
        else:
            cursor = conn.execute(
                "INSERT INTO devices (device_key) VALUES (?)", (device_key,)
            )
            return cursor.lastrowid

    def _save_schedule(
        self,
        conn: sqlite3.Connection,
        device_id: int,
        schedule_data: Dict[str, Any],
        is_today: bool,
        updated_on: Optional[str] = None,
    ) -> Optional[int]:
        """Сохраняем расписание и временные слоты"""
        if not schedule_data or not schedule_data.get("slots"):
            return None

        # Извлекаем дату из строки с таймзоной
        date_str = schedule_data.get("date", "")
        schedule_date = date_str.split("T")[0] if "T" in date_str else ""

        if not schedule_date:
            return None

        # Извлекаем смещение таймзоны
        timezone_offset = None
        if "T" in date_str and "+" in date_str:
            timezone_offset = date_str.split("+")[1]

        # Проверяем, существует ли уже расписание для этой даты (независимо от is_today)
        cursor = conn.execute(
            """
            SELECT id FROM outage_schedules 
            WHERE device_id = ? AND schedule_date = ?
        """,
            (device_id, schedule_date),
        )

        existing = cursor.fetchone()

        if existing:
            # Обновляем существующее расписание (перезаписываем is_today на текущее значение)
            schedule_id = existing[0]
            conn.execute(
                """
                UPDATE outage_schedules 
                SET status = ?, timezone_offset = ?, updated_on = ?, is_today = ?
                WHERE id = ?
            """,
                (
                    schedule_data.get("status", ""),
                    timezone_offset,
                    updated_on,
                    is_today,
                    schedule_id,
                ),
            )

            # Удаляем старые слоты
            conn.execute("DELETE FROM time_slots WHERE schedule_id = ?", (schedule_id,))
        else:
            # Создаем новое расписание
            cursor = conn.execute(
                """
                INSERT INTO outage_schedules 
                (device_id, schedule_date, status, timezone_offset, is_today, updated_on)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    device_id,
                    schedule_date,
                    schedule_data.get("status", ""),
                    timezone_offset,
                    is_today,
                    updated_on,
                ),
            )
            schedule_id = cursor.lastrowid
        # Удалим любые другие строки расписания для этой же device_id/schedule_date (чтобы не оставались дубликаты)
        conn.execute(
            "DELETE FROM outage_schedules WHERE device_id = ? AND schedule_date = ? AND id <> ?",
            (device_id, schedule_date, schedule_id),
        )
        # Сохраняем временные слоты
        slots = schedule_data.get("slots", [])
        for slot in slots:
            conn.execute(
                """
                INSERT INTO time_slots (schedule_id, start_minute, end_minute, slot_type)
                VALUES (?, ?, ?, ?)
            """,
                (
                    schedule_id,
                    slot.get("start", 0),
                    slot.get("end", 0),
                    slot.get("type", ""),
                ),
            )

        return schedule_id

    def _normalize_schedules(self, conn: sqlite3.Connection) -> None:
        """Нормализует таблицы outage_schedules и time_slots.

        Для каждой пары (device_id, schedule_date) оставляет самую свежую
        запись расписания (по updated_on), собирает уникальные слоты из всех
        дубликатов и записывает их как единый набор слотов.
        """
        cursor = conn.execute(
            "SELECT device_id, schedule_date, COUNT(*) as c FROM outage_schedules GROUP BY device_id, schedule_date HAVING c > 1"
        )
        groups = cursor.fetchall()

        for g in groups:
            device_id = g[0]
            schedule_date = g[1]

            # Получаем все расписания для этой пары
            rows = conn.execute(
                "SELECT id, status, timezone_offset, is_today, updated_on FROM outage_schedules WHERE device_id = ? AND schedule_date = ?",
                (device_id, schedule_date),
            ).fetchall()

            if not rows:
                continue

            # Выбираем запись с максимальным updated_on (если updated_on нет, используем минимальную дату)
            def parsed_upd(r):
                u = r[4]
                try:
                    return datetime.fromisoformat(u) if u else datetime.min
                except Exception:
                    return datetime.min

            rows_sorted = sorted(rows, key=parsed_upd, reverse=True)
            keep = rows_sorted[0]
            keep_id = keep[0]

            # Собираем уникальные слоты со всех schedule_id
            slot_set = set()
            for r in rows:
                sid = r[0]
                for s in conn.execute(
                    "SELECT start_minute, end_minute, slot_type FROM time_slots WHERE schedule_id = ?",
                    (sid,),
                ).fetchall():
                    slot_set.add((s[0], s[1], s[2]))

            # Удаляем все расписания для этой пары (каскадно удалятся слоты)
            conn.execute(
                "DELETE FROM outage_schedules WHERE device_id = ? AND schedule_date = ?",
                (device_id, schedule_date),
            )

            # Вставляем одну запись (используем данные из keep)
            conn.execute(
                "INSERT INTO outage_schedules (device_id, schedule_date, status, timezone_offset, is_today, updated_on) VALUES (?, ?, ?, ?, ?, ?)",
                (device_id, schedule_date, keep[1], keep[2], keep[3], keep[4]),
            )
            new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # Вставляем объединённые уникальные слоты
            for st, en, typ in sorted(slot_set):
                conn.execute(
                    "INSERT INTO time_slots (schedule_id, start_minute, end_minute, slot_type) VALUES (?, ?, ?, ?)",
                    (new_id, st, en, typ),
                )

    def get_device_schedule(
        self,
        device_key: str,
        date: Optional[str] = None,
        is_today: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """
        Получение расписания для устройства

        Args:
            device_key: Ключ устройства (например "1.1")
            date: Дата в формате YYYY-MM-DD (опционально)
            is_today: True для today, False для tomorrow (опционально)

        Returns:
            List[Dict]: Список расписаний с временными слотами
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            query = """
                SELECT 
                    d.device_key,
                    os.schedule_date,
                    os.status,
                    os.timezone_offset,
                    os.is_today,
                    os.updated_on,
                    ts.start_minute,
                    ts.end_minute,
                    ts.slot_type
                FROM devices d
                JOIN outage_schedules os ON d.id = os.device_id
                JOIN time_slots ts ON os.id = ts.schedule_id
                WHERE d.device_key = ?
            """
            params = [device_key]

            if date:
                query += " AND os.schedule_date = ?"
                params.append(date)

            if is_today is not None:
                query += " AND os.is_today = ?"
                params.append(is_today)

            query += " ORDER BY os.schedule_date, os.is_today, ts.start_minute"

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

            return [dict(row) for row in rows]

    def get_all_devices_with_schedules(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Получение всех устройств с их расписаниями

        Returns:
            Dict: Словарь с данными всех устройств
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.execute("""
                SELECT 
                    d.device_key,
                    os.schedule_date,
                    os.status,
                    os.timezone_offset,
                    os.is_today,
                    os.updated_on,
                    ts.start_minute,
                    ts.end_minute,
                    ts.slot_type
                FROM devices d
                LEFT JOIN outage_schedules os ON d.id = os.device_id
                LEFT JOIN time_slots ts ON os.id = ts.schedule_id
                ORDER BY d.device_key, os.schedule_date, os.is_today, ts.start_minute
            """)

            rows = cursor.fetchall()

            # Группируем результаты по устройствам
            result = {}
            for row in rows:
                device_key = row["device_key"]
                if device_key not in result:
                    result[device_key] = []

                if row["schedule_date"]:  # Если есть расписание
                    result[device_key].append(dict(row))

            return result

    def clear_all_data(self):
        """Очистка всех данных в базе"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM time_slots")
            conn.execute("DELETE FROM outage_schedules")
            conn.execute("DELETE FROM devices")

    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики по базе данных"""
        with sqlite3.connect(self.db_path) as conn:
            stats = {}

            # Количество устройств
            cursor = conn.execute("SELECT COUNT(*) FROM devices")
            stats["devices_count"] = cursor.fetchone()[0]

            # Количество расписаний
            cursor = conn.execute("SELECT COUNT(*) FROM outage_schedules")
            stats["schedules_count"] = cursor.fetchone()[0]

            # Количество временных слотов
            cursor = conn.execute("SELECT COUNT(*) FROM time_slots")
            stats["slots_count"] = cursor.fetchone()[0]

            # Количество слотов по типам
            cursor = conn.execute("""
                SELECT slot_type, COUNT(*) 
                FROM time_slots 
                GROUP BY slot_type
            """)
            stats["slots_by_type"] = {row[0]: row[1] for row in cursor.fetchall()}

            # Последнее обновление
            cursor = conn.execute("""
                SELECT MAX(updated_on) FROM outage_schedules 
                WHERE updated_on IS NOT NULL
            """)
            stats["last_updated"] = cursor.fetchone()[0]

            return stats
