import io
import json
import tempfile
import unittest
from pathlib import Path

from app import FoodOrderingApp


class TestClient:
    def __init__(self, application):
        self.application = application
        self.cookie = ""
        self.csrf_token = ""

    def request(self, method, path, body=None, csrf=True):
        raw_body = json.dumps(body or {}).encode("utf-8")
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(raw_body) if body is not None else 0),
            "CONTENT_TYPE": "application/json",
            "wsgi.input": io.BytesIO(raw_body),
        }
        if self.cookie:
            environ["HTTP_COOKIE"] = self.cookie
        if csrf and method not in ("GET", "HEAD") and self.csrf_token:
            environ["HTTP_X_CSRF_TOKEN"] = self.csrf_token
        captured = {}

        def start_response(status, headers):
            captured["status"] = int(status.split(" ", 1)[0])
            captured["headers"] = dict(headers)

        response = b"".join(self.application(environ, start_response))
        set_cookie = captured["headers"].get("Set-Cookie")
        if set_cookie:
            self.cookie = set_cookie.split(";", 1)[0]
        payload = json.loads(response) if response else {}
        if "csrfToken" in payload:
            self.csrf_token = payload["csrfToken"]
        return captured["status"], payload

    def initialize_session(self):
        return self.request("GET", "/api/session")


class FoodOrderingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "orders.sqlite3"
        self.application = FoodOrderingApp(database_path)
        self.client = TestClient(self.application)
        status, _ = self.client.initialize_session()
        self.assertEqual(status, 200)

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_customer(self):
        return self.client.request(
            "POST",
            "/api/register",
            {
                "name": "Олена",
                "email": "olena@example.com",
                "phone": "+380501234567",
                "password": "secure-pass-123",
            },
        )

    def create_operator(self):
        operator = TestClient(self.application)
        status, _ = operator.initialize_session()
        self.assertEqual(status, 200)
        status, data = operator.request(
            "POST",
            "/api/setup/operator",
            {
                "name": "Оператор",
                "email": "operator@example.com",
                "phone": "+380671234567",
                "password": "operator-pass-123",
            },
        )
        self.assertEqual(status, 201, data)
        return operator

    def make_order(self, key="confirm-123"):
        _, slots = self.client.request("GET", "/api/slots")
        return self.client.request(
            "POST",
            "/api/orders",
            {
                "slotId": slots["slots"][0]["id"],
                "idempotencyKey": key,
                "items": [
                    {
                        "dishId": 2,
                        "quantity": 2,
                        "modifierIds": [3],
                        "unitPriceCents": 1,
                    }
                ],
            },
        )

    def test_server_calculates_prices_and_duplicate_confirmation_is_idempotent(self):
        status, _ = self.create_customer()
        self.assertEqual(status, 201)

        status, response = self.make_order()

        self.assertEqual(status, 201)
        order = response["order"]
        self.assertEqual(order["total_cents"], 36800)
        self.assertEqual(order["status"], "new")

        status, repeated = self.make_order()

        self.assertEqual(status, 200)
        self.assertEqual(repeated["order"]["id"], order["id"])
        self.assertEqual(repeated["order"]["total_cents"], 36800)

    def test_customer_cannot_mark_order_completed(self):
        self.create_customer()
        status, response = self.make_order()
        self.assertEqual(status, 201)

        status, result = self.client.request(
            "PATCH",
            f"/api/orders/{response['order']['id']}/status",
            {"status": "completed"},
        )

        self.assertEqual(status, 403)
        self.assertIn("Клієнт може скасувати", result["error"])

    def test_closed_restaurant_rejects_checkout(self):
        self.create_customer()
        operator = self.create_operator()
        status, _ = operator.request(
            "PATCH", "/api/operator/restaurant", {"acceptingOrders": False}
        )
        self.assertEqual(status, 200)

        status, response = self.make_order()

        self.assertEqual(status, 409)
        self.assertIn("не приймає замовлення", response["error"])

    def test_unavailable_dish_cannot_be_ordered(self):
        self.create_customer()
        operator = self.create_operator()
        status, _ = operator.request(
            "PATCH", "/api/operator/dishes/2", {"isAvailable": False}
        )
        self.assertEqual(status, 200)

        status, response = self.make_order()

        self.assertEqual(status, 409)
        self.assertIn("більше недоступна", response["error"])

    def test_pickup_slot_capacity_is_enforced(self):
        self.create_customer()
        _, slots = self.client.request("GET", "/api/slots")
        slot_id = slots["slots"][0]["id"]
        with self.application.connect() as db:
            db.execute(
                "UPDATE pickup_slots SET capacity = 1 WHERE id = ?", (slot_id,)
            )

        status, _ = self.client.request(
            "POST",
            "/api/orders",
            {
                "slotId": slot_id,
                "idempotencyKey": "first-order",
                "items": [{"dishId": 2, "quantity": 1, "modifierIds": []}],
            },
        )
        self.assertEqual(status, 201)

        status, response = self.client.request(
            "POST",
            "/api/orders",
            {
                "slotId": slot_id,
                "idempotencyKey": "second-order",
                "items": [{"dishId": 2, "quantity": 1, "modifierIds": []}],
            },
        )

        self.assertEqual(status, 409)
        self.assertIn("більше немає місць", response["error"])

    def test_modifier_limit_is_checked_on_server(self):
        self.create_customer()
        _, slots = self.client.request("GET", "/api/slots")

        status, response = self.client.request(
            "POST",
            "/api/orders",
            {
                "slotId": slots["slots"][0]["id"],
                "idempotencyKey": "too-many-options",
                "items": [{"dishId": 1, "quantity": 1, "modifierIds": [1, 2, 3, 4]}],
            },
        )

        self.assertEqual(status, 400)
        self.assertIn("не більше 3", response["error"])

    def test_customer_cannot_change_operator_menu(self):
        self.create_customer()

        status, response = self.client.request(
            "PATCH", "/api/operator/dishes/1", {"isAvailable": False}
        )

        self.assertEqual(status, 403)
        self.assertIn("Недостатньо прав", response["error"])

    def test_operator_bootstrap_is_available_only_once(self):
        self.create_operator()
        second_operator = TestClient(self.application)
        second_operator.initialize_session()

        status, response = second_operator.request(
            "POST",
            "/api/setup/operator",
            {
                "name": "Інший оператор",
                "email": "other@example.com",
                "phone": "+380631234567",
                "password": "different-pass-123",
            },
        )

        self.assertEqual(status, 409)
        self.assertIn("вже створено", response["error"])


if __name__ == "__main__":
    unittest.main()
