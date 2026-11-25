from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
import time
from typing import Any
from urllib.parse import parse_qs

from app_common import ApiError, SESSION_COOKIE, SESSION_LIFETIME, json_response, now_local
from database import DatabaseMixin, ROOT
from orders import OrderMixin


MAX_REQUEST_SIZE = 64 * 1024
LOGGER = logging.getLogger("smachno")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1
    )
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, digest_hex = stored_hash.split("$", 1)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=2**14,
            r=8,
            p=1,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


class FoodOrderingApp(DatabaseMixin, OrderMixin):


    @staticmethod
    def cookie_token(environ: dict[str, Any]) -> str | None:
        cookie_header = environ.get("HTTP_COOKIE", "")
        for part in cookie_header.split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name == SESSION_COOKIE:
                return value
        return None

    @staticmethod
    def token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def get_session(self, environ: dict[str, Any]) -> sqlite3.Row | None:
        token = self.cookie_token(environ)
        if not token:
            return None
        with self.connect() as db:
            session = db.execute(
                """SELECT sessions.token_hash, sessions.csrf_token, sessions.user_id,
                users.name, users.email, users.phone, users.role
                FROM sessions LEFT JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
                (self.token_digest(token), int(time.time())),
            ).fetchone()
            return session

    def ensure_session(self, environ: dict[str, Any]) -> tuple[sqlite3.Row, list[tuple[str, str]]]:
        existing = self.get_session(environ)
        if existing:
            return existing, []
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        with self.connect() as db:
            db.execute(
                "INSERT INTO sessions (token_hash, csrf_token, expires_at) VALUES (?, ?, ?)",
                (self.token_digest(token), csrf, int(time.time()) + SESSION_LIFETIME),
            )
        return (
            self.get_session({"HTTP_COOKIE": f"{SESSION_COOKIE}={token}"}),
            [self.session_cookie(token)],
        )

    @staticmethod
    def session_cookie(token: str) -> tuple[str, str]:
        secure = "; Secure" if os.environ.get("COOKIE_SECURE") == "1" else ""
        return (
            "Set-Cookie",
            f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_LIFETIME}{secure}",
        )

    @staticmethod
    def expired_cookie() -> tuple[str, str]:
        return (
            "Set-Cookie",
            f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
        )

    @staticmethod
    def read_json(environ: dict[str, Any]) -> dict[str, Any]:
        try:
            length = int(environ.get("CONTENT_LENGTH") or "0")
        except ValueError as exc:
            raise ApiError(400, "Некоректний розмір запиту.") from exc
        if length < 0 or length > MAX_REQUEST_SIZE:
            raise ApiError(413, "Запит завеликий.")
        raw_body = environ["wsgi.input"].read(length)
        try:
            data = json.loads(raw_body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, "Надішліть коректні дані у форматі JSON.") from exc
        if not isinstance(data, dict):
            raise ApiError(400, "Очікується JSON-об’єкт.")
        return data

    @staticmethod
    def require_text(
        data: dict[str, Any], key: str, label: str, minimum: int, maximum: int
    ) -> str:
        value = data.get(key)
        if not isinstance(value, str):
            raise ApiError(400, f"Поле «{label}» є обов’язковим.")
        value = value.strip()
        if not minimum <= len(value) <= maximum:
            raise ApiError(
                400, f"Поле «{label}» має містити від {minimum} до {maximum} символів."
            )
        return value

    @staticmethod
    def require_email(data: dict[str, Any]) -> str:
        email = FoodOrderingApp.require_text(
            data, "email", "електронна пошта", 5, 254
        ).lower()
        if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            raise ApiError(400, "Введіть коректну електронну пошту.")
        return email

    @staticmethod
    def require_csrf(session: sqlite3.Row, environ: dict[str, Any]) -> None:
        supplied = environ.get("HTTP_X_CSRF_TOKEN", "")
        if not supplied or not hmac.compare_digest(supplied, session["csrf_token"]):
            raise ApiError(403, "Сесію оновлено. Перезавантажте сторінку та повторіть дію.")

    @staticmethod
    def require_role(session: sqlite3.Row, role: str | None = None) -> None:
        if session["user_id"] is None:
            raise ApiError(401, "Увійдіть у свій обліковий запис.")
        if role and session["role"] != role:
            raise ApiError(403, "Недостатньо прав для цієї дії.")

    @staticmethod
    def public_user(session: sqlite3.Row | None) -> dict[str, Any] | None:
        if not session or session["user_id"] is None:
            return None
        return {
            "id": session["user_id"],
            "name": session["name"],
            "email": session["email"],
            "phone": session["phone"],
            "role": session["role"],
        }

    def authenticate(
        self, session: sqlite3.Row, user_id: int
    ) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (session["token_hash"],))
            db.execute(
                "INSERT INTO sessions (token_hash, csrf_token, user_id, expires_at) VALUES (?, ?, ?, ?)",
                (self.token_digest(token), csrf, user_id, int(time.time()) + SESSION_LIFETIME),
            )
        return token, csrf


    def handle_api(
        self, method: str, path: str, environ: dict[str, Any], start_response: Any
    ) -> list[bytes]:
        query = parse_qs(environ.get("QUERY_STRING", ""))
        if method == "GET" and path == "/api/session":
            session, headers = self.ensure_session(environ)
            with self.connect() as db:
                open_status = self.is_open(db)
                setup_allowed = db.execute(
                    "SELECT 1 FROM users WHERE role = 'operator' LIMIT 1"
                ).fetchone() is None
            return json_response(
                start_response,
                200,
                {
                    "user": self.public_user(session),
                    "csrfToken": session["csrf_token"],
                    "restaurantOpen": open_status,
                    "operatorSetupAllowed": setup_allowed,
                },
                headers,
            )

        session = self.get_session(environ)
        if method == "GET" and path == "/api/menu":
            raw_category = query.get("category", [None])[0]
            category_id = None
            if raw_category:
                try:
                    category_id = int(raw_category)
                except ValueError as exc:
                    raise ApiError(400, "Некоректна категорія меню.") from exc
            with self.connect() as db:
                categories = [
                    dict(row)
                    for row in db.execute(
                        "SELECT id, name FROM categories ORDER BY sort_order"
                    ).fetchall()
                ]
                dishes = self.menu_data(
                    db, query.get("q", [""])[0].strip()[:80], category_id
                )
            return json_response(
                start_response, 200, {"categories": categories, "dishes": dishes}
            )

        if method == "GET" and path == "/api/slots":
            with self.connect() as db:
                slots = self.pickup_slots(db)
            return json_response(start_response, 200, {"slots": slots})

        if method == "POST" and path in ("/api/register", "/api/login", "/api/setup/operator"):
            if not session:
                raise ApiError(403, "Сесія відсутня. Перезавантажте сторінку.")
            self.require_csrf(session, environ)
            data = self.read_json(environ)
            if path == "/api/register":
                name = self.require_text(data, "name", "ім’я", 2, 80)
                email = self.require_email(data)
                phone = self.require_text(data, "phone", "телефон", 7, 30)
                password = self.require_text(data, "password", "пароль", 8, 128)
                with self.connect() as db:
                    try:
                        cursor = db.execute(
                            """INSERT INTO users (name, email, phone, password_hash, role, created_at)
                            VALUES (?, ?, ?, ?, 'customer', ?)""",
                            (name, email, phone, hash_password(password), now_local().isoformat(timespec="seconds")),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise ApiError(409, "Обліковий запис із такою поштою вже існує.") from exc
                    user_id = cursor.lastrowid
                token, csrf = self.authenticate(session, user_id)
                user_session = self.get_session({"HTTP_COOKIE": f"{SESSION_COOKIE}={token}"})
                return json_response(
                    start_response, 201,
                    {"user": self.public_user(user_session), "csrfToken": csrf},
                    [self.session_cookie(token)],
                )
            if path == "/api/login":
                email = self.require_email(data)
                password = self.require_text(data, "password", "пароль", 1, 128)
                with self.connect() as db:
                    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
                if not user or not verify_password(password, user["password_hash"]):
                    raise ApiError(401, "Неправильна електронна пошта або пароль.")
                token, csrf = self.authenticate(session, user["id"])
                user_session = self.get_session({"HTTP_COOKIE": f"{SESSION_COOKIE}={token}"})
                return json_response(
                    start_response, 200,
                    {"user": self.public_user(user_session), "csrfToken": csrf},
                    [self.session_cookie(token)],
                )
            if path == "/api/setup/operator":
                name = self.require_text(data, "name", "ім’я", 2, 80)
                email = self.require_email(data)
                phone = self.require_text(data, "phone", "телефон", 7, 30)
                password = self.require_text(data, "password", "пароль", 10, 128)
                with self.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if db.execute(
                        "SELECT 1 FROM users WHERE role = 'operator' LIMIT 1"
                    ).fetchone():
                        raise ApiError(409, "Обліковий запис оператора вже створено.")
                    try:
                        cursor = db.execute(
                            """INSERT INTO users (name, email, phone, password_hash, role, created_at)
                            VALUES (?, ?, ?, ?, 'operator', ?)""",
                            (name, email, phone, hash_password(password), now_local().isoformat(timespec="seconds")),
                        )
                    except sqlite3.IntegrityError as exc:
                        raise ApiError(409, "Обліковий запис із такою поштою вже існує.") from exc
                    user_id = cursor.lastrowid
                    db.execute(
                        "UPDATE sessions SET user_id = ? WHERE token_hash = ?",
                        (user_id, session["token_hash"]),
                    )
                authenticated = self.get_session(environ)
                return json_response(
                    start_response, 201,
                    {"user": self.public_user(authenticated), "csrfToken": authenticated["csrf_token"]},
                )

        if method == "POST" and path == "/api/logout":
            if not session:
                return json_response(start_response, 200, {"ok": True}, [self.expired_cookie()])
            self.require_csrf(session, environ)
            with self.connect() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (session["token_hash"],))
            return json_response(start_response, 200, {"ok": True}, [self.expired_cookie()])

        if method == "GET" and path == "/api/orders":
            if not session:
                raise ApiError(401, "Увійдіть, щоб переглянути замовлення.")
            self.require_role(session)
            with self.connect() as db:
                if session["role"] == "operator":
                    rows = db.execute(
                        "SELECT id FROM orders ORDER BY created_at DESC LIMIT 100"
                    ).fetchall()
                else:
                    rows = db.execute(
                        "SELECT id FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
                        (session["user_id"],),
                    ).fetchall()
                orders = [self.order_data(db, row["id"]) for row in rows]
            return json_response(start_response, 200, {"orders": orders})

        if method == "POST" and path == "/api/orders":
            if not session:
                raise ApiError(401, "Увійдіть, щоб оформити замовлення.")
            self.require_role(session, "customer")
            self.require_csrf(session, environ)
            data = self.read_json(environ)
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                status, order = self.place_order(db, session["user_id"], data)
            return json_response(start_response, status, {"order": order})

        order_status_match = re.fullmatch(r"/api/orders/(\d+)/status", path)
        if method == "PATCH" and order_status_match:
            if not session:
                raise ApiError(401, "Увійдіть у свій обліковий запис.")
            self.require_role(session)
            self.require_csrf(session, environ)
            data = self.read_json(environ)
            with self.connect() as db:
                order = self.update_order_status(
                    db, session, int(order_status_match.group(1)), data
                )
            return json_response(start_response, 200, {"order": order})

        dish_match = re.fullmatch(r"/api/operator/dishes/(\d+)", path)
        if method == "PATCH" and dish_match:
            if not session:
                raise ApiError(401, "Увійдіть у свій обліковий запис.")
            self.require_role(session, "operator")
            self.require_csrf(session, environ)
            data = self.read_json(environ)
            if "isAvailable" not in data or not isinstance(data["isAvailable"], bool):
                raise ApiError(400, "Вкажіть доступність страви.")
            with self.connect() as db:
                cursor = db.execute(
                    "UPDATE dishes SET is_available = ? WHERE id = ?",
                    (int(data["isAvailable"]), int(dish_match.group(1))),
                )
                if cursor.rowcount == 0:
                    raise ApiError(404, "Страву не знайдено.")
            return json_response(start_response, 200, {"ok": True})

        if method == "PATCH" and path == "/api/operator/restaurant":
            if not session:
                raise ApiError(401, "Увійдіть у свій обліковий запис.")
            self.require_role(session, "operator")
            self.require_csrf(session, environ)
            data = self.read_json(environ)
            if "acceptingOrders" not in data or not isinstance(data["acceptingOrders"], bool):
                raise ApiError(400, "Вкажіть стан приймання замовлень.")
            with self.connect() as db:
                db.execute(
                    "UPDATE restaurant_settings SET accepting_orders = ? WHERE id = 1",
                    (int(data["acceptingOrders"]),),
                )
            return json_response(start_response, 200, {"restaurantOpen": data["acceptingOrders"]})

        raise ApiError(404, "Сторінку або дію не знайдено.")

    def serve_static(self, path: str, start_response: Any) -> list[bytes]:
        if path in ("/", "/index.html"):
            file_path = ROOT / "templates" / "index.html"
        elif path == "/static/app.js":
            file_path = ROOT / "static" / "app.js"
        elif path == "/static/styles.css":
            file_path = ROOT / "static" / "styles.css"
        else:
            raise ApiError(404, "Сторінку не знайдено.")
        try:
            body = file_path.read_bytes()
        except OSError as exc:
            LOGGER.exception("Не вдалося прочитати файл інтерфейсу: %s", file_path)
            raise ApiError(500, "Не вдалося завантажити інтерфейс.") from exc
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        start_response(
            "200 OK",
            [
                ("Content-Type", f"{content_type}; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("X-Content-Type-Options", "nosniff"),
                ("Referrer-Policy", "same-origin"),
                ("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'self'; frame-ancestors 'none'"),
            ],
        )
        return [body]

    def __call__(self, environ: dict[str, Any], start_response: Any) -> list[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        try:
            if path.startswith("/api/"):
                return self.handle_api(method, path, environ, start_response)
            if method != "GET":
                raise ApiError(405, "Для цієї сторінки доступний лише перегляд.")
            return self.serve_static(path, start_response)
        except ApiError as exc:
            return json_response(start_response, exc.status, {"error": exc.message})
        except sqlite3.Error:
            LOGGER.exception("Помилка бази даних під час обробки %s %s", method, path)
            return json_response(
                start_response, 500, {"error": "Помилка сховища. Спробуйте пізніше."}
            )
        except Exception:
            LOGGER.exception("Неочікувана помилка під час обробки %s %s", method, path)
            return json_response(
                start_response, 500, {"error": "Не вдалося виконати запит. Спробуйте ще раз."}
            )


application = FoodOrderingApp()
