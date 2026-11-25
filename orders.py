from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from app_common import ApiError, MAX_MODIFIERS_PER_DISH, ORDER_STATUSES, STATUS_TRANSITIONS, now_local

class OrderMixin:

    @staticmethod
    def is_open(db: sqlite3.Connection) -> bool:
        row = db.execute(
            "SELECT accepting_orders FROM restaurant_settings WHERE id = 1"
        ).fetchone()
        return bool(row and row["accepting_orders"])

    @staticmethod
    def menu_data(db: sqlite3.Connection, query: str, category_id: int | None) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if query:
            clauses.append("(d.name LIKE ? OR d.description LIKE ? OR d.ingredients LIKE ?)")
            needle = f"%{query}%"
            params.extend([needle, needle, needle])
        if category_id is not None:
            clauses.append("d.category_id = ?")
            params.append(category_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = db.execute(
            f"""SELECT d.*, c.name AS category_name FROM dishes d
            JOIN categories c ON c.id = d.category_id {where}
            ORDER BY c.sort_order, d.name""",
            params,
        ).fetchall()
        dishes = []
        for row in rows:
            item = dict(row)
            item["is_available"] = bool(item["is_available"])
            item["modifiers"] = [
                dict(modifier)
                for modifier in db.execute(
                    "SELECT id, name, price_cents FROM modifiers WHERE dish_id = ? ORDER BY id",
                    (row["id"],),
                ).fetchall()
            ]
            dishes.append(item)
        return dishes

    def pickup_slots(self, db: sqlite3.Connection) -> list[dict[str, Any]]:
        self.ensure_pickup_slots(db)
        rows = db.execute(
            """SELECT s.id, s.starts_at, s.capacity,
            s.capacity - COUNT(o.id) AS remaining
            FROM pickup_slots s LEFT JOIN orders o
            ON o.slot_id = s.id AND o.status <> 'cancelled'
            WHERE s.starts_at > ? AND s.is_open = 1
            GROUP BY s.id HAVING remaining > 0
            ORDER BY s.starts_at LIMIT 80""",
            (now_local().isoformat(timespec="minutes"),),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def order_data(db: sqlite3.Connection, order_id: int) -> dict[str, Any]:
        row = db.execute(
            """SELECT o.*, s.starts_at AS pickup_at, u.name AS customer_name,
            u.email AS customer_email, u.phone AS customer_phone
            FROM orders o JOIN pickup_slots s ON s.id = o.slot_id
            JOIN users u ON u.id = o.user_id WHERE o.id = ?""",
            (order_id,),
        ).fetchone()
        if not row:
            raise ApiError(404, "Замовлення не знайдено.")
        result = dict(row)
        result["status_label"] = ORDER_STATUSES[result["status"]]
        result["items"] = []
        items = db.execute(
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,)
        ).fetchall()
        for item in items:
            item_data = dict(item)
            item_data["modifiers"] = [
                dict(modifier)
                for modifier in db.execute(
                    "SELECT modifier_name AS name, price_cents FROM order_item_modifiers WHERE order_item_id = ?",
                    (item["id"],),
                ).fetchall()
            ]
            result["items"].append(item_data)
        return result

    def place_order(
        self, db: sqlite3.Connection, user_id: int, data: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        key = self.require_text(data, "idempotencyKey", "ключ підтвердження", 8, 100)
        existing = db.execute(
            "SELECT id FROM orders WHERE user_id = ? AND idempotency_key = ?",
            (user_id, key),
        ).fetchone()
        if existing:
            return 200, self.order_data(db, existing["id"])
        if not self.is_open(db):
            raise ApiError(409, "Заклад зараз не приймає замовлення.")
        slot_id = data.get("slotId")
        if not isinstance(slot_id, int) or isinstance(slot_id, bool):
            raise ApiError(400, "Оберіть інтервал отримання.")
        note = data.get("note", "")
        if not isinstance(note, str) or len(note.strip()) > 300:
            raise ApiError(400, "Коментар має містити не більше 300 символів.")
        items = data.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 30:
            raise ApiError(400, "Кошик має містити від 1 до 30 позицій.")
        slot = db.execute(
            "SELECT * FROM pickup_slots WHERE id = ? AND is_open = 1", (slot_id,)
        ).fetchone()
        if not slot or datetime.fromisoformat(slot["starts_at"]) <= now_local() + timedelta(minutes=5):
            raise ApiError(409, "Цей інтервал уже недоступний. Оберіть інший.")
        occupied = db.execute(
            "SELECT COUNT(*) FROM orders WHERE slot_id = ? AND status <> 'cancelled'",
            (slot_id,),
        ).fetchone()[0]
        if occupied >= slot["capacity"]:
            raise ApiError(409, "На цей інтервал більше немає місць.")

        normalized_items = []
        total_cents = 0
        for item in items:
            if not isinstance(item, dict):
                raise ApiError(400, "Некоректна позиція кошика.")
            dish_id = item.get("dishId")
            quantity = item.get("quantity")
            modifier_ids = item.get("modifierIds", [])
            if not isinstance(dish_id, int) or isinstance(dish_id, bool):
                raise ApiError(400, "Некоректна страва у кошику.")
            if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 20:
                raise ApiError(400, "Кількість кожної страви має бути від 1 до 20.")
            if (
                not isinstance(modifier_ids, list)
                or len(modifier_ids) > MAX_MODIFIERS_PER_DISH
                or any(not isinstance(value, int) or isinstance(value, bool) for value in modifier_ids)
                or len(set(modifier_ids)) != len(modifier_ids)
            ):
                raise ApiError(400, f"Для страви можна обрати не більше {MAX_MODIFIERS_PER_DISH} різних модифікаторів.")
            dish = db.execute(
                """SELECT id, name, price_cents, is_available FROM dishes WHERE id = ?""",
                (dish_id,),
            ).fetchone()
            if not dish or not dish["is_available"]:
                raise ApiError(409, "Одна зі страв більше недоступна. Оновіть меню.")
            modifiers = []
            if modifier_ids:
                placeholders = ",".join("?" for _ in modifier_ids)
                modifiers = db.execute(
                    f"""SELECT id, name, price_cents FROM modifiers
                    WHERE dish_id = ? AND id IN ({placeholders})""",
                    [dish_id, *modifier_ids],
                ).fetchall()
                if len(modifiers) != len(modifier_ids):
                    raise ApiError(400, "Обраний модифікатор не належить цій страві.")
            unit_total = dish["price_cents"] + sum(
                modifier["price_cents"] for modifier in modifiers
            )
            total_cents += unit_total * quantity
            normalized_items.append((dish, quantity, modifiers))

        created_at = now_local().isoformat(timespec="seconds")
        cursor = db.execute(
            """INSERT INTO orders
            (user_id, slot_id, status, total_cents, note, idempotency_key, created_at, updated_at)
            VALUES (?, ?, 'new', ?, ?, ?, ?, ?)""",
            (user_id, slot_id, total_cents, note.strip(), key, created_at, created_at),
        )
        order_id = cursor.lastrowid
        for dish, quantity, modifiers in normalized_items:
            item_cursor = db.execute(
                """INSERT INTO order_items
                (order_id, dish_id, dish_name, unit_price_cents, quantity)
                VALUES (?, ?, ?, ?, ?)""",
                (order_id, dish["id"], dish["name"], dish["price_cents"], quantity),
            )
            db.executemany(
                """INSERT INTO order_item_modifiers
                (order_item_id, modifier_name, price_cents) VALUES (?, ?, ?)""",
                [
                    (item_cursor.lastrowid, modifier["name"], modifier["price_cents"])
                    for modifier in modifiers
                ],
            )
        return 201, self.order_data(db, order_id)

    def update_order_status(
        self, db: sqlite3.Connection, session: sqlite3.Row, order_id: int, data: dict[str, Any]
    ) -> dict[str, Any]:
        order = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order:
            raise ApiError(404, "Замовлення не знайдено.")
        requested_status = data.get("status")
        if not isinstance(requested_status, str) or requested_status not in ORDER_STATUSES:
            raise ApiError(400, "Некоректний статус замовлення.")
        if session["role"] == "customer":
            if order["user_id"] != session["user_id"]:
                raise ApiError(404, "Замовлення не знайдено.")
            if requested_status != "cancelled" or order["status"] != "new":
                raise ApiError(403, "Клієнт може скасувати лише нове замовлення.")
        elif session["role"] != "operator":
            raise ApiError(403, "Недостатньо прав для цієї дії.")
        if requested_status not in STATUS_TRANSITIONS[order["status"]]:
            raise ApiError(409, "Цей перехід між станами замовлення неможливий.")
        db.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
            (requested_status, now_local().isoformat(timespec="seconds"), order_id),
        )
        return self.order_data(db, order_id)
