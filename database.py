from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app_common import now_local

ROOT = Path(__file__).resolve().parent

class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()

class DatabaseMixin:

    def __init__(self, database_path: str | Path | None = None):
        configured_path = database_path or os.environ.get(
            "DATABASE_PATH", str(ROOT / "data" / "orders.sqlite3")
        )
        self.database_path = Path(configured_path)
        if str(self.database_path) != ":memory:":
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize_database()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.database_path), timeout=10, factory=ClosingConnection
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def initialize_database(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    phone TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('customer', 'operator')),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    csrf_token TEXT NOT NULL,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    sort_order INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS dishes (
                    id INTEGER PRIMARY KEY,
                    category_id INTEGER NOT NULL REFERENCES categories(id),
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    ingredients TEXT NOT NULL,
                    allergens TEXT NOT NULL,
                    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
                    is_available INTEGER NOT NULL DEFAULT 1 CHECK (is_available IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS modifiers (
                    id INTEGER PRIMARY KEY,
                    dish_id INTEGER NOT NULL REFERENCES dishes(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    price_cents INTEGER NOT NULL CHECK (price_cents >= 0)
                );
                CREATE TABLE IF NOT EXISTS pickup_slots (
                    id INTEGER PRIMARY KEY,
                    starts_at TEXT NOT NULL UNIQUE,
                    capacity INTEGER NOT NULL CHECK (capacity > 0),
                    is_open INTEGER NOT NULL DEFAULT 1 CHECK (is_open IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS restaurant_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    accepting_orders INTEGER NOT NULL DEFAULT 1 CHECK (accepting_orders IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    slot_id INTEGER NOT NULL REFERENCES pickup_slots(id),
                    status TEXT NOT NULL CHECK (status IN ('new', 'preparing', 'ready', 'completed', 'cancelled')),
                    total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
                    note TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (user_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS order_items (
                    id INTEGER PRIMARY KEY,
                    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                    dish_id INTEGER NOT NULL REFERENCES dishes(id),
                    dish_name TEXT NOT NULL,
                    unit_price_cents INTEGER NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 20)
                );
                CREATE TABLE IF NOT EXISTS order_item_modifiers (
                    id INTEGER PRIMARY KEY,
                    order_item_id INTEGER NOT NULL REFERENCES order_items(id) ON DELETE CASCADE,
                    modifier_name TEXT NOT NULL,
                    price_cents INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_orders_user_created ON orders(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_orders_status_created ON orders(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_slots_starts_at ON pickup_slots(starts_at);
                INSERT OR IGNORE INTO restaurant_settings (id, accepting_orders) VALUES (1, 1);
                """
            )
            count = db.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
            if count == 0:
                self.seed_menu(db)
            self.ensure_pickup_slots(db)

    @staticmethod
    def seed_menu(db: sqlite3.Connection) -> None:
        categories = [
            (1, "Основні страви", 1),
            (2, "Супи", 2),
            (3, "Салати", 3),
            (4, "Напої", 4),
        ]
        db.executemany(
            "INSERT INTO categories (id, name, sort_order) VALUES (?, ?, ?)",
            categories,
        )
        dishes = [
            (1, 1, "Борщ український", "Традиційний борщ зі сметаною та зеленню.", "Буряк, капуста, картопля, морква, квасоля, сметана", "молоко", 18900, 1),
            (2, 1, "Вареники з картоплею", "Домашні вареники з підсмаженою цибулею.", "Пшеничне борошно, картопля, цибуля, олія", "глютен", 16900, 1),
            (3, 1, "Куряча котлета з пюре", "Соковита куряча котлета з картопляним пюре.", "Курятина, картопля, вершкове масло, молоко", "молоко, яйця", 22900, 1),
            (4, 2, "Крем-суп із гарбуза", "Ніжний сезонний суп із гарбуза.", "Гарбуз, вершки, цибуля, овочевий бульйон", "молоко", 14900, 1),
            (5, 3, "Салат із печеним буряком", "Буряк, рукола та насіння з легкою заправкою.", "Буряк, рукола, насіння соняшника, олія", "насіння соняшника", 17900, 1),
            (6, 4, "Узвар домашній", "Напій із сушених фруктів без доданого цукру.", "Сушені яблука, груші, чорнослив, вода", "не вказані", 6500, 1),
        ]
        db.executemany(
            """INSERT INTO dishes
            (id, category_id, name, description, ingredients, allergens, price_cents, is_available)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            dishes,
        )
        db.executemany(
            "INSERT INTO modifiers (dish_id, name, price_cents) VALUES (?, ?, ?)",
            [
                (1, "Сметана", 1500),
                (1, "Додаткова зелень", 500),
                (2, "Сметана", 1500),
                (2, "Підсмажена цибуля", 700),
                (3, "Додаткова порція пюре", 2500),
                (4, "Грінки", 1200),
                (5, "Додаткове насіння", 800),
            ],
        )

    @staticmethod
    def ensure_pickup_slots(db: sqlite3.Connection) -> None:
        current = now_local()
        start_day = current.date()
        for day_offset in range(7):
            date = start_day + timedelta(days=day_offset)
            for minutes in range(11 * 60, 21 * 60, 30):
                slot_time = datetime(
                    date.year,
                    date.month,
                    date.day,
                    minutes // 60,
                    minutes % 60,
                    tzinfo=current.tzinfo,
                )
                if slot_time <= current + timedelta(minutes=10):
                    continue
                db.execute(
                    """INSERT OR IGNORE INTO pickup_slots (starts_at, capacity, is_open)
                    VALUES (?, 8, 1)""",
                    (slot_time.isoformat(timespec="minutes"),),
                )
