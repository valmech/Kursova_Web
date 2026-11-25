from __future__ import annotations

from datetime import datetime
from http import HTTPStatus
from typing import Any
import json

SESSION_COOKIE = "smachno_session"

SESSION_LIFETIME = 60 * 60 * 24 * 7

MAX_MODIFIERS_PER_DISH = 3

ORDER_STATUSES = {
    "new": "Нове",
    "preparing": "Готується",
    "ready": "Готове",
    "completed": "Виконане",
    "cancelled": "Скасоване",
}

STATUS_TRANSITIONS = {
    "new": {"preparing", "cancelled"},
    "preparing": {"ready", "cancelled"},
    "ready": {"completed"},
    "completed": set(),
    "cancelled": set(),
}

class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message

def now_local() -> datetime:
    return datetime.now().astimezone()

def json_response(
    start_response: Any,
    status: int,
    payload: dict[str, Any],
    headers: list[tuple[str, str]] | None = None,
) -> list[bytes]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response_headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "same-origin"),
        ("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'self'; frame-ancestors 'none'"),
    ]
    if headers:
        response_headers.extend(headers)
    start_response(f"{status} {HTTPStatus(status).phrase}", response_headers)
    return [body]
