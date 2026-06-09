"""Locust – czysty wyścig o 1 pokój (tylko book, bez cancel/search).

Scenariusz: wielu użytkowników jednocześnie rezerwuje h-waw-1 / single.
Przy seed --rooms 1 dokładnie 1 request powinien wygrać, reszta NO_AVAILABILITY.

Przepływ (z katalogu hotel_reservation):
    .\\tests\\load\\run_contention.ps1 -Pure

Lub ręcznie:
    python tests/load/seed_hotels.py --rooms 1
    python tests/load/verify_contention.py --save-baseline
    locust -f tests/load/locustfile_pure_contention.py --host http://localhost:8000 --headless -u 20 -r 20 -t 30s
    python tests/load/verify_contention.py --seeded-rooms 1 --hotel h-waw-1 --room-type single
"""

from __future__ import annotations

import random
import uuid
from typing import Optional

from locust import HttpUser, between, task
from locust.exception import RescheduleTask

# Jeden hotel, jeden typ – wyścig o dokładnie 1 pokój (przy seed --rooms 1)
TARGET_HOTEL = "h-waw-1"
TARGET_ROOM = "single"


class PureContentionUser(HttpUser):
    """Tylko rezerwacja – bez anulowań i odczytów."""

    wait_time = between(0, 0.1)

    def on_start(self) -> None:
        n = random.randint(1, 50)
        self.username = f"user{n}"
        self.token: Optional[str] = None
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

    @task
    def book_room(self) -> None:
        if not self.token:
            raise RescheduleTask()

        payload = {
            "user_id": self.username,
            "hotel_id": TARGET_HOTEL,
            "room_type": TARGET_ROOM,
            "nights": 1,
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
                    resp.success()
                    self.environment.events.request.fire(
                        request_type="POST",
                        name="/reservations [book] → OK",
                        response_time=resp.elapsed.total_seconds() * 1000,
                        response_length=len(resp.content),
                        exception=None,
                        context={},
                    )
                else:
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
                self._login()
                resp.failure("re-login")
            else:
                resp.failure(f"HTTP {resp.status_code}")
