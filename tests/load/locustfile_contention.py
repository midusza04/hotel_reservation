"""Locust – contention test (TYLKO HotelUser, bez AdminUser).

Używaj tego pliku do testowania wyścigu o ostatni pokój:

    # 1. Zatrzymaj stare Locusty (albo: .\\tests\\load\\run_contention.ps1)
    Get-Process locust -ErrorAction SilentlyContinue | Stop-Process -Force

    # 2. Dosiej pokoje
    python tests/load/seed_hotels.py --rooms 1

    # 3. Baseline PO seed, PRZED testem
    python tests/load/verify_contention.py --save-baseline

    # 4. Uruchom test
    locust -f tests/load/locustfile_contention.py --host http://localhost:8000 --headless -u 20 -r 20 -t 30s --csv tests/load/results/contention

    # 5. Zweryfikuj
    python tests/load/verify_contention.py --seeded-rooms 1
"""

from __future__ import annotations

import random
import uuid
from typing import Optional

from locust import HttpUser, between, task
from locust.exception import RescheduleTask

HOTELS = [
    {"id": "h-waw-1", "rooms": ["single", "double"]},
    {"id": "h-krk-1", "rooms": ["single", "double", "apartment"]},
]


class HotelUser(HttpUser):
    wait_time = between(0.3, 1.5)

    def on_start(self) -> None:
        n = random.randint(1, 50)
        self.username = f"user{n}"
        self.token: Optional[str] = None
        self.reservations: list[str] = []
        self._login()

    def _login(self) -> None:
        with self.client.post(
            "/auth/login",
            json={"username": self.username, "password": "pass"},
            catch_response=True,
            name="/auth/login",
        ) as resp:
            if resp.status_code == 200:
                self.token = resp.json()["access_token"]
                self.client.headers["Authorization"] = f"Bearer {self.token}"
                resp.success()
            else:
                resp.failure(f"Login failed {resp.status_code}")

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @task(6)
    def book_room(self) -> None:
        if not self.token:
            raise RescheduleTask()
        hotel = random.choice(HOTELS)
        payload = {
            "user_id": self.username,
            "hotel_id": hotel["id"],
            "room_type": random.choice(hotel["rooms"]),
            "nights": random.randint(1, 3),
            "payment_method": "card",
            "idempotency_key": str(uuid.uuid4()),
        }
        with self.client.post(
            "/reservations",
            json=payload,
            headers=self._auth(),
            catch_response=True,
            name="/reservations [book]",
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok") and data.get("reservation_id"):
                    self.reservations.append(data["reservation_id"])
                    resp.success()
                    self.environment.events.request.fire(
                        request_type="POST",
                        name="/reservations [book] → OK",
                        response_time=resp.elapsed.total_seconds() * 1000,
                        response_length=len(resp.content),
                        exception=None, context={},
                    )
                else:
                    resp.success()
                    self.environment.events.request.fire(
                        request_type="POST",
                        name="/reservations [book] → NO_AVAILABILITY",
                        response_time=resp.elapsed.total_seconds() * 1000,
                        response_length=len(resp.content),
                        exception=None, context={},
                    )
            elif resp.status_code in (401, 403):
                self._login()
                resp.failure("re-login")
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(3)
    def cancel_reservation(self) -> None:
        if not self.token or not self.reservations:
            raise RescheduleTask()
        res_id = self.reservations.pop(random.randrange(len(self.reservations)))
        with self.client.post(
            "/reservations/cancel",
            json={"user_id": self.username, "reservation_id": res_id},
            headers=self._auth(),
            catch_response=True,
            name="/reservations/cancel",
        ) as resp:
            resp.success() if resp.status_code == 200 else resp.failure(f"HTTP {resp.status_code}")

    @task(4)
    def search_hotels(self) -> None:
        self.client.post("/hotels/search", json={}, name="/hotels/search")

    @task(2)
    def check_history(self) -> None:
        if not self.token:
            raise RescheduleTask()
        self.client.get(
            f"/users/{self.username}/reservations",
            headers=self._auth(),
            name="/users/{id}/reservations",
        )
