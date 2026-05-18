"""
Integration tests for the FastAPI application.

The app's lifespan is patched so actors are created as real Ray remotes
(local Ray, no cluster, no DB).  Tests use httpx.AsyncClient against the
ASGI app directly — no network required.
"""

import sys
import os
import types
import asyncio
import pytest
import pytest_asyncio
import ray
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Paths & mock DB (must happen before any app import)
# ---------------------------------------------------------------------------
APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, os.path.abspath(APP_DIR))

# conftest already patches sys.modules["db"], but be safe
if "db" not in sys.modules:
    db = types.ModuleType("db")
    db.init_db = lambda: None
    db.load_hotels = lambda: []
    db.load_reservations = lambda: []
    db.save_hotel_snapshot = lambda **kw: None
    db.save_reservation = lambda r: None
    db.update_reservation_status = lambda *a, **kw: None
    db.write_audit_log = lambda e: None
    db.load_audit_logs = lambda **kw: []
    sys.modules["db"] = db


# ---------------------------------------------------------------------------
# Build a test FastAPI app with real (local) Ray actors but no DB / cluster
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="module")
async def client():
    import ray
    from actors import (
        InventoryActor, PaymentActor, ReservationHistoryActor,
        AuditLogActor, MetricsActor, BookingCoordinatorActor, AdminActor,
    )
    import main as _main

    app = _main.app

    # Create anonymous actors for tests — don't touch the production named actors
    inventory  = InventoryActor.remote()
    payment    = PaymentActor.remote()
    history    = ReservationHistoryActor.remote()
    audit_log  = AuditLogActor.remote()
    metrics    = MetricsActor.remote()
    coordinator = BookingCoordinatorActor.remote(inventory, payment, history, audit_log, metrics)
    admin       = AdminActor.remote(inventory, audit_log)

    # Seed a test hotel so booking tests have something to work with
    ray.get(admin.upsert_hotel.remote(
        hotel_id="h-test-warszawa",
        name="Test Hotel Warszawa",
        city="Warszawa",
        rooms={
            "single": {"available": 5, "price": 100.0},
            "double": {"available": 3, "price": 200.0},
        },
    ))

    # Wire app.state manually — bypasses the lifespan entirely
    app.state.inventory   = inventory
    app.state.payment     = payment
    app.state.history     = history
    app.state.coordinator = coordinator
    app.state.admin       = admin
    app.state.audit_log   = audit_log
    app.state.metrics     = metrics

    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    # Kill test actors so they don't linger in the cluster
    for actor in [inventory, payment, history, audit_log, metrics, coordinator, admin]:
        try:
            ray.kill(actor)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
async def _login(client, username="user1", password="pass"):
    resp = await client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# Tests: Authentication
# ===========================================================================
class TestAuth:
    @pytest.mark.asyncio
    async def test_login_success_user(self, client):
        resp = await client.post("/auth/login", json={"username": "user1", "password": "pass"})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_login_success_admin(self, client):
        resp = await client.post("/auth/login", json={"username": "admin", "password": "admin"})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client):
        resp = await client.post("/auth/login", json={"username": "user1", "password": "wrong"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_unknown_user(self, client):
        resp = await client.post("/auth/login", json={"username": "nobody", "password": "pass"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_protected_endpoint_no_token(self, client):
        resp = await client.get("/users/user1/reservations")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_protected_endpoint_invalid_token(self, client):
        resp = await client.get(
            "/users/user1/reservations",
            headers={"Authorization": "Bearer not-a-valid-token"},
        )
        assert resp.status_code in (401, 403)


# ===========================================================================
# Tests: Health
# ===========================================================================
class TestHealth:
    @pytest.mark.asyncio
    async def test_health(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_nodes" in data


# ===========================================================================
# Tests: Hotel search
# ===========================================================================
class TestHotels:
    @pytest.mark.asyncio
    async def test_search_all_returns_list(self, client):
        resp = await client.post("/hotels/search", json={})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_search_by_city(self, client):
        resp = await client.post("/hotels/search", json={"city": "Warszawa"})
        assert resp.status_code == 200
        hotels = resp.json()
        for h in hotels:
            assert h["city"] == "Warszawa"

    @pytest.mark.asyncio
    async def test_search_nonexistent_city(self, client):
        resp = await client.post("/hotels/search", json={"city": "Atlantis"})
        assert resp.status_code == 200
        assert resp.json() == []


# ===========================================================================
# Tests: Full booking flow
# ===========================================================================
class TestBookingFlow:
    @pytest.mark.asyncio
    async def test_full_booking_and_cancel(self, client):
        token = await _login(client)

        # 1. Find a hotel
        hotels = (await client.post("/hotels/search", json={})).json()
        assert hotels, "No hotels seeded — check _seed_data in main.py"
        hotel = hotels[0]
        room_type = next(iter(hotel["rooms"]))

        # 2. Book
        resp = await client.post(
            "/reservations",
            json={
                "user_id": "user1",
                "hotel_id": hotel["hotel_id"],
                "room_type": room_type,
                "nights": 2,
                "payment_method": "card",
            },
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text
        booking = resp.json()
        assert booking["ok"]
        assert "reservation_id" in booking
        reservation_id = booking["reservation_id"]

        # 3. Check history
        hist = (
            await client.get("/users/user1/reservations", headers=_auth(token))
        ).json()
        ids = [r["reservation_id"] for r in hist["reservations"]]
        assert reservation_id in ids

        # 4. Cancel
        cancel = await client.post(
            "/reservations/cancel",
            json={"user_id": "user1", "reservation_id": reservation_id},
            headers=_auth(token),
        )
        assert cancel.status_code == 200
        assert cancel.json()["ok"]

        # 5. Verify cancelled in history
        hist2 = (
            await client.get("/users/user1/reservations", headers=_auth(token))
        ).json()
        statuses = {r["reservation_id"]: r["status"] for r in hist2["reservations"]}
        assert statuses[reservation_id] == "cancelled"

    @pytest.mark.asyncio
    async def test_booking_with_invalid_room_type(self, client):
        token = await _login(client)
        hotels = (await client.post("/hotels/search", json={})).json()
        hotel = hotels[0]

        resp = await client.post(
            "/reservations",
            json={
                "user_id": "user1",
                "hotel_id": hotel["hotel_id"],
                "room_type": "nonexistent-room",
                "nights": 1,
                "payment_method": "card",
            },
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert not resp.json()["ok"]

    @pytest.mark.asyncio
    async def test_booking_rejected_payment(self, client):
        token = await _login(client)
        hotels = (await client.post("/hotels/search", json={})).json()
        hotel = hotels[0]
        room_type = next(iter(hotel["rooms"]))

        resp = await client.post(
            "/reservations",
            json={
                "user_id": "user1",
                "hotel_id": hotel["hotel_id"],
                "room_type": room_type,
                "nights": 1,
                "payment_method": "reject",
            },
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert not resp.json()["ok"]

    @pytest.mark.asyncio
    async def test_cannot_book_other_users_reservation(self, client):
        """user1 cannot cancel user2's reservation."""
        token_user1 = await _login(client, "user1", "pass")
        # user2 doesn't exist in fake users, use admin as second user
        token_admin = await _login(client, "admin", "admin")

        hotels = (await client.post("/hotels/search", json={})).json()
        hotel = hotels[0]
        room_type = next(iter(hotel["rooms"]))

        # admin books
        booking = (
            await client.post(
                "/reservations",
                json={
                    "user_id": "admin",
                    "hotel_id": hotel["hotel_id"],
                    "room_type": room_type,
                    "nights": 1,
                    "payment_method": "card",
                },
                headers=_auth(token_admin),
            )
        ).json()
        assert booking["ok"], booking

        # user1 tries to cancel admin's reservation
        cancel = await client.post(
            "/reservations/cancel",
            json={"user_id": "user1", "reservation_id": booking["reservation_id"]},
            headers=_auth(token_user1),
        )
        # Either 403 from FastAPI or ok:False from coordinator
        body = cancel.json()
        assert cancel.status_code == 403 or not body.get("ok")


# ===========================================================================
# Tests: Admin endpoints
# ===========================================================================
class TestAdmin:
    @pytest.mark.asyncio
    async def test_add_hotel_requires_admin(self, client):
        token = await _login(client, "user1", "pass")
        resp = await client.post(
            "/admin/hotels",
            json={
                "hotel_id": "h-test-new",
                "name": "Test Hotel",
                "city": "Testowo",
                "rooms": {"single": {"available": 5, "price": 99}},
            },
            headers=_auth(token),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_add_hotel_as_admin(self, client):
        token = await _login(client, "admin", "admin")
        resp = await client.post(
            "/admin/hotels",
            json={
                "hotel_id": "h-integration-test",
                "name": "Integration Hotel",
                "city": "Testowo",
                "rooms": {"single": {"available": 5, "price": 99}},
            },
            headers=_auth(token),
        )
        assert resp.status_code == 200

        # Verify it appears in search
        found = (await client.post("/hotels/search", json={"city": "Testowo"})).json()
        names = [h["name"] for h in found]
        assert "Integration Hotel" in names

    @pytest.mark.asyncio
    async def test_audit_logs_requires_admin(self, client):
        token = await _login(client, "user1", "pass")
        resp = await client.get("/admin/audit-logs", headers=_auth(token))
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_audit_logs_as_admin(self, client):
        token = await _login(client, "admin", "admin")
        resp = await client.get("/admin/audit-logs", headers=_auth(token))
        assert resp.status_code == 200
        assert "entries" in resp.json()


# ===========================================================================
# Tests: Overbooking prevention
# ===========================================================================
class TestOverbooking:
    @pytest.mark.asyncio
    async def test_no_overbooking_sequential(self, client):
        """Exhaust all rooms of one type then verify next booking is rejected."""
        token = await _login(client, "admin", "admin")

        # Add a hotel with exactly 1 room
        await client.post(
            "/admin/hotels",
            json={
                "hotel_id": "h-overbooking-test",
                "name": "Tiny Hotel",
                "city": "Mikrograd",
                "rooms": {"single": {"available": 1, "price": 50}},
            },
            headers=_auth(token),
        )

        user_token = await _login(client, "user1", "pass")

        # First booking — should succeed
        r1 = (
            await client.post(
                "/reservations",
                json={
                    "user_id": "user1",
                    "hotel_id": "h-overbooking-test",
                    "room_type": "single",
                    "nights": 1,
                    "payment_method": "card",
                },
                headers=_auth(user_token),
            )
        ).json()
        assert r1["ok"], r1

        # Second booking — same room, should fail
        r2 = (
            await client.post(
                "/reservations",
                json={
                    "user_id": "user1",
                    "hotel_id": "h-overbooking-test",
                    "room_type": "single",
                    "nights": 1,
                    "payment_method": "card",
                },
                headers=_auth(user_token),
            )
        ).json()
        assert not r2["ok"]
