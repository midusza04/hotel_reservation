"""Locust load-test scenarios for the Ray Hotel Reservation System.

Scenarios
---------
HotelUser  (regular guest)
    Simulates a guest who logs in, searches hotels, books a room, occasionally
    checks history, and sometimes cancels a reservation.  Weight mix is designed
    to be read-heavy (search 40 %) with moderate booking (30 %) and lighter
    cancellation / history reads (15 % each).

AdminUser  (operator)
    Reads audit logs and metrics.  Hotel restock (bump_hotel_capacity) is OFF
    by default – set env LOCUST_RESTOCK=1 to enable (not for contention tests).

Run (against a locally started cluster on port 8000)
----------------------------------------------------
    pip install locust
    locust -f locustfile.py --host http://localhost:8000

    # headless, 30 users, 5/s ramp, 60 s duration, CSV report:
    locust -f locustfile.py --host http://localhost:8000 \\
           --headless -u 30 -r 5 -t 60s \\
           --csv results/run1

    # Before a high-concurrency run, pre-seed more rooms:
    python seed_hotels.py --host http://localhost:8000 --rooms 200
"""

from __future__ import annotations

import os
import random
import uuid
from typing import Optional

from locust import HttpUser, between, events, task
from locust.exception import RescheduleTask

# ──────────────────────────────────────────────────────────────────────────────
# Static test data
# ──────────────────────────────────────────────────────────────────────────────

HOTELS = [
    {"id": "h-waw-1", "rooms": ["single", "double"]},
    {"id": "h-krk-1", "rooms": ["single", "double", "apartment"]},
]

_RESTOCK_WEIGHT = (
    1 if os.getenv("LOCUST_RESTOCK", "").lower() in ("1", "true", "yes") else 0
)

PAYMENT_METHODS = ["card", "cash"]

# Credentials generated in main.py: user1..user50 / pass
NUM_USERS = 50


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _random_user() -> tuple[str, str]:
    """Return a (username, password) pair for a random test user."""
    n = random.randint(1, NUM_USERS)
    return f"user{n}", "pass"


def _booking_payload(username: str) -> dict:
    hotel = random.choice(HOTELS)
    return {
        "user_id": username,
        "hotel_id": hotel["id"],
        "room_type": random.choice(hotel["rooms"]),
        "nights": random.randint(1, 4),
        "payment_method": random.choice(PAYMENT_METHODS),
        "idempotency_key": str(uuid.uuid4()),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Regular guest user
# ──────────────────────────────────────────────────────────────────────────────

class HotelUser(HttpUser):
    """Guest user performing realistic hotel reservation flows."""

    wait_time = between(0.3, 1.5)

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def on_start(self) -> None:
        self.username, password = _random_user()
        self.token: Optional[str] = None
        self.reservations: list[str] = []
        self._login(self.username, password)

    def _login(self, username: str, password: str) -> None:
        with self.client.post(
            "/auth/login",
            json={"username": username, "password": password},
            catch_response=True,
            name="/auth/login",
        ) as resp:
            if resp.status_code == 200:
                self.token = resp.json()["access_token"]
                self.client.headers.update({"Authorization": f"Bearer {self.token}"})
                resp.success()
            else:
                resp.failure(f"Login failed: {resp.status_code}")

    def _auth_headers(self) -> dict:
        if not self.token:
            self._login(self.username, "pass")
        return {"Authorization": f"Bearer {self.token}"}

    # ── tasks ──────────────────────────────────────────────────────────────────

    @task(8)
    def search_hotels(self) -> None:
        """Search with random filters – most common read operation."""
        filters: dict = {}
        r = random.random()
        if r < 0.4:
            filters["city"] = random.choice(["Warszawa", "Krakow"])
        elif r < 0.6:
            filters["max_price"] = random.choice([250.0, 350.0, 600.0])
        elif r < 0.75:
            filters["room_type"] = random.choice(["single", "double", "apartment"])
        # else: empty payload → return all hotels

        with self.client.post(
            "/hotels/search",
            json=filters,
            catch_response=True,
            name="/hotels/search",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Search failed: {resp.status_code}")

    @task(6)
    def book_room(self) -> None:
        """Try to create a reservation – core booking flow."""
        if not self.token:
            raise RescheduleTask()

        payload = _booking_payload(self.username)

        with self.client.post(
            "/reservations",
            json=payload,
            headers=self._auth_headers(),
            catch_response=True,
            name="/reservations [book]",
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok") and data.get("reservation_id"):
                    # Liczymy oddzielnie udane rezerwacje w statystykach Locust
                    self.reservations.append(data["reservation_id"])
                    resp.success()
                    # Dodatkowy wpis w statystykach jako osobny "endpoint"
                    self.environment.events.request.fire(
                        request_type="POST",
                        name="/reservations [book] → OK",
                        response_time=resp.elapsed.total_seconds() * 1000,
                        response_length=len(resp.content),
                        exception=None,
                        context={},
                    )
                else:
                    # Brak dostępności – poprawna odpowiedź biznesowa, nie błąd
                    resp.success()
                    self.environment.events.request.fire(
                        request_type="POST",
                        name="/reservations [book] → NO_AVAILABILITY",
                        response_time=resp.elapsed.total_seconds() * 1000,
                        response_length=len(resp.content),
                        exception=None,
                        context={},
                    )
            elif resp.status_code in (401, 403):
                self._login(self.username, "pass")
                resp.failure("Re-login triggered")
            else:
                resp.failure(f"Unexpected status {resp.status_code}: {resp.text[:120]}")

    @task(3)
    def cancel_reservation(self) -> None:
        """Cancel an owned reservation."""
        if not self.token or not self.reservations:
            raise RescheduleTask()

        res_id = self.reservations.pop(random.randrange(len(self.reservations)))

        with self.client.post(
            "/reservations/cancel",
            json={"user_id": self.username, "reservation_id": res_id},
            headers=self._auth_headers(),
            catch_response=True,
            name="/reservations/cancel",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code in (401, 403):
                self._login(self.username, "pass")
                resp.failure("Re-login triggered")
            else:
                resp.failure(f"Cancel failed {resp.status_code}: {resp.text[:120]}")

    @task(3)
    def check_history(self) -> None:
        """Read user reservation history."""
        if not self.token:
            raise RescheduleTask()

        with self.client.get(
            f"/users/{self.username}/reservations",
            headers=self._auth_headers(),
            catch_response=True,
            name="/users/{user_id}/reservations",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code in (401, 403):
                self._login(self.username, "pass")
                resp.failure("Re-login triggered")
            else:
                resp.failure(f"History failed {resp.status_code}")

    @task(1)
    def health_check(self) -> None:
        """Periodic health probe – mimics monitoring / load-balancer."""
        with self.client.get(
            "/health",
            catch_response=True,
            name="/health",
        ) as resp:
            if resp.status_code == 200 and resp.json().get("status") == "ok":
                resp.success()
            else:
                resp.failure(f"Health check failed: {resp.status_code}")

    @task(2)
    def idempotent_double_book(self) -> None:
        """Send the SAME booking request twice – second must return the same result."""
        if not self.token:
            raise RescheduleTask()

        payload = _booking_payload(self.username)
        first_id: Optional[str] = None

        for attempt in range(2):
            with self.client.post(
                "/reservations",
                json=payload,
                headers=self._auth_headers(),
                catch_response=True,
                name="/reservations [idempotent]",
            ) as resp:
                if resp.status_code != 200:
                    resp.failure(f"Idempotency attempt {attempt} failed: {resp.status_code}")
                    return
                data = resp.json()
                res_id = data.get("reservation_id")
                if attempt == 0:
                    first_id = res_id
                    resp.success()
                else:
                    if first_id and res_id and first_id != res_id:
                        resp.failure(
                            f"Idempotency broken: first={first_id} second={res_id}"
                        )
                    else:
                        resp.success()

        # Keep track for potential cancellation
        if first_id:
            self.reservations.append(first_id)


# ──────────────────────────────────────────────────────────────────────────────
# Admin / operator user  (spawn at low rate)
# ──────────────────────────────────────────────────────────────────────────────

class AdminUser(HttpUser):
    """Operator user: reads audit logs and bumps hotel capacity."""

    wait_time = between(2.0, 5.0)
    weight = 1  # small fraction of the user pool

    def on_start(self) -> None:
        self.token: Optional[str] = None
        with self.client.post(
            "/auth/login",
            json={"username": "admin", "password": "admin"},
            name="/auth/login [admin]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                self.token = resp.json()["access_token"]
                self.client.headers.update({"Authorization": f"Bearer {self.token}"})
                resp.success()
            else:
                resp.failure(f"Admin login failed: {resp.status_code}")

    def _ah(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @task(4)
    def read_audit_logs(self) -> None:
        params = {"limit": 50}
        event_types = [None, "RESERVATION_CREATED", "RESERVATION_CANCELLED",
                       "PAYMENT_SUCCESS", "LOGIN"]
        et = random.choice(event_types)
        if et:
            params["event_type"] = et

        with self.client.get(
            "/admin/audit-logs",
            params=params,
            headers=self._ah(),
            catch_response=True,
            name="/admin/audit-logs",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Audit logs failed: {resp.status_code}")

    @task(_RESTOCK_WEIGHT)
    def bump_hotel_capacity(self) -> None:
        """Re-seed hotel capacity – only when LOCUST_RESTOCK=1."""
        hotel_id = random.choice(["h-waw-1", "h-krk-1"])
        payload = {
            "hotel_id": hotel_id,
            "name": "Warsaw Central Hotel" if hotel_id == "h-waw-1" else "Krakow Market Suites",
            "city": "Warszawa" if hotel_id == "h-waw-1" else "Krakow",
            "rooms": {
                "single": {"available": random.randint(50, 120), "price": 220.0},
                "double": {"available": random.randint(30, 80), "price": 340.0},
            },
        }
        if hotel_id == "h-krk-1":
            payload["rooms"]["apartment"] = {
                "available": random.randint(10, 30),
                "price": 520.0,
            }

        with self.client.post(
            "/admin/hotels",
            json=payload,
            headers=self._ah(),
            catch_response=True,
            name="/admin/hotels [upsert]",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Hotel upsert failed: {resp.status_code}")

    @task(2)
    def get_metrics(self) -> None:
        with self.client.get(
            "/metrics",
            catch_response=True,
            name="/metrics",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Metrics failed: {resp.status_code}")


# ──────────────────────────────────────────────────────────────────────────────
# Event hooks – summary printed after headless runs
# ──────────────────────────────────────────────────────────────────────────────

@events.quitting.add_listener
def _on_quitting(environment, **_kwargs) -> None:
    stats = environment.runner.stats
    total = stats.total
    print("\n" + "─" * 60)
    print(f"  Requests:        {total.num_requests}")
    print(f"  Failures:        {total.num_failures}  "
          f"({100 * total.fail_ratio:.1f} %)")
    print(f"  Median latency:  {total.median_response_time} ms")
    print(f"  p95  latency:    {total.get_response_time_percentile(0.95):.0f} ms")
    print(f"  p99  latency:    {total.get_response_time_percentile(0.99):.0f} ms")
    print(f"  RPS:             {total.current_rps:.1f}")
    print("─" * 60)
